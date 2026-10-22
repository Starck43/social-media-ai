"""Authenticate API requests and bind their authorization scope.

Workspace endpoints require a current active web membership. Tenant headers
select among those memberships and never expand access. Account self-service
requires authentication without a workspace. Platform catalog/account endpoints
require an operator identity and retain their per-action permission checks.

The middleware establishes tenant and permission contexts; managers and endpoint
checks enforce the corresponding resource boundaries.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import jwt
from jwt import PyJWTError as JWTError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings
from app.core.permissions import permission_scope, platform_request_scope
from app.core.tenant_context import tenant_scope

logger = logging.getLogger(__name__)

API_PREFIX = "/api"

# Paths reachable without a token: obtaining a token, the infra probe, and the
# public OAuth callback (the platform redirects the user's browser there after
# consent — it cannot carry a bearer token; the signed `state` protects it).
PUBLIC_API_PATHS = frozenset(
    {
        "/api/v1/auth/login",
        "/api/v1/auth/refresh-token",
        "/api/v1/auth/register",
        "/api/v1/health",
        "/api/v1/social/callback",
    }
)

TENANT_ID_HEADER = "x-tenant-id"
TENANT_SLUG_HEADER = "x-tenant-slug"


def _is_public(path: str) -> bool:
    return path.rstrip("/") in PUBLIC_API_PATHS


class ApiScopeMiddleware:
    """Authenticate `/api/*` and run it inside the caller's workspace scope."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith(API_PREFIX) or _is_public(path):
            await self.app(scope, receive, send)
            return

        request = Request(scope)

        user = await self._authenticate(request)
        if user is None or not user.is_active:
            await self._unauthorized(scope, receive, send)
            return

        if path.rstrip("/") in {"/api/v1/users/me", "/api/v1/auth/change-password"}:
            request.state.api_user = user
            with tenant_scope(), permission_scope(user):
                await self.app(scope, receive, send)
            return

        if path.startswith(("/api/v1/llm-models", "/api/v1/llm-providers")):
            request.state.api_user = user
            with tenant_scope(), permission_scope(user):
                await self.app(scope, receive, send)
            return

        operator_path = (
            path.startswith("/api/v1/users/roles")
            or (path.startswith("/api/v1/users") and path.rstrip("/") != "/api/v1/users/me")
        )
        if operator_path:
            if not user.has_admin_access():
                await JSONResponse({"detail": "Operator access required"}, status_code=403)(scope, receive, send)
                return
            request.state.api_user = user
            request.state.operator_context = True
            # Endpoints retain per-action checks; no workspace membership is implied.
            with tenant_scope(bypass=True), platform_request_scope(), permission_scope(user):
                await self.app(scope, receive, send)
            return

        tenant_id, error, grants = await self._resolve_tenant(request, user)
        if tenant_id is None:
            logger.warning("API access denied for user %s: %s", getattr(user, "username", "?"), error)
            await JSONResponse({"detail": error or "No workspace access"}, status_code=403)(scope, receive, send)
            return

        request.state.api_user = user
        request.state.tenant_id = tenant_id

        # Everything below — endpoints, managers, background tasks — sees exactly
        # one workspace through the queryset guard in BaseManager. A non-owner
        # member's workspace rights come from the membership role.
        with tenant_scope(tenant_id), permission_scope(user, member_grants=grants):
            await self.app(scope, receive, send)

    # ── authentication ──────────────────────────────────────────────────────

    async def _authenticate(self, request: Request) -> Optional[Any]:
        """Resolve the caller from the bearer token; None when not authenticated.

        The role and its permissions are eager-loaded because the session that
        fetched the row is closed before the request body runs — a lazy
        `user.role` access here would raise a DetachedInstanceError.
        """
        from app.models import User

        header = request.headers.get("authorization") or ""
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return None

        try:
            payload = jwt.decode(token.strip(), settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        except JWTError:
            return None
        if payload.get("type") != "access":
            return None
        subject = payload.get("sub")
        if subject is None:
            return None

        try:
            user_id = int(subject)
        except (TypeError, ValueError):
            return None

        return await User.objects.prefetch_related("role.permissions").get(id=user_id)

    # ── workspace resolution ────────────────────────────────────────────────

    async def _resolve_tenant(self, request: Request, user: Any) -> tuple[Optional[int], Optional[str], Any]:
        """`(tenant_id, error, member_grants)` — the workspace to run in, or why not.

        `member_grants` is `TenantUserManager.member_grants()` of the selected
        membership: None for an owner, the membership role's rights otherwise.

        Never returns a workspace the caller is not a member of, whatever the
        headers say.
        """
        from app.models.managers.tenant_manager import TenantUserManager, tenants

        memberships = await TenantUserManager().web_memberships(user.id)
        if not memberships:
            return None, "User has no workspace membership", None

        allowed = {m.tenant_id for m in memberships}

        raw_id = (request.headers.get(TENANT_ID_HEADER) or "").strip()
        raw_slug = (request.headers.get(TENANT_SLUG_HEADER) or "").strip()
        if not raw_id and not raw_slug:
            # Default: the first workspace the caller belongs to (same order the
            # web UI uses). Members of several workspaces select another one with
            # the headers.
            return memberships[0].tenant_id, None, await TenantUserManager().member_grants(memberships[0])

        if raw_id.isdigit():
            tenant = await tenants.get(id=int(raw_id))
        else:
            tenant = await tenants.get_by_slug(raw_slug)
        if tenant is None:
            return None, "Workspace not found", None
        if tenant.id not in allowed:
            return None, "User is not a member of this workspace", None
        membership = next(m for m in memberships if m.tenant_id == tenant.id)
        return tenant.id, None, await TenantUserManager().member_grants(membership)

    @staticmethod
    async def _unauthorized(scope: Scope, receive: Receive, send: Send) -> None:
        await JSONResponse(
            {"detail": "Not authenticated"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )(scope, receive, send)
