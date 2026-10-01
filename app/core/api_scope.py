"""API scope: authentication + workspace isolation for the HTTP API.

Two HTTP surfaces exist and they must not be confused:

- `/api/*` is the **client-facing API**. Every request is authenticated (bearer
  JWT) and runs inside the tenant scope of a workspace the caller is an *active
  member of*. `BaseManager` then filters every tenant-scoped model, so a user
  can only ever reach their own workspace rows — never anybody else's.
- `/admin/*` (sqladmin) and the **CLI** are the operator/developer console.
  They run with `tenant_scope(bypass=True)` and deliberately see everything.

This middleware owns `/api/*`: it authenticates once, resolves the workspace
once, and hands the rest of the stack a ready `tenant_scope`. Endpoints then
never have to remember to filter by tenant — and can never forget to.

The workspace is picked from the caller's `tenant_users` web memberships (the
same source `/app/*` uses, not JWT claims). Callers with several workspaces may
select one with the `X-Tenant-Id` / `X-Tenant-Slug` header; a workspace the
caller is not a member of is refused — the headers never widen access.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from jose import JWTError, jwt
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings
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
        if user is None:
            await self._unauthorized(scope, receive, send)
            return

        tenant_id, error = await self._resolve_tenant(request, user)
        if tenant_id is None:
            logger.warning("API access denied for user %s: %s", getattr(user, "username", "?"), error)
            await JSONResponse({"detail": error or "No workspace access"}, status_code=403)(scope, receive, send)
            return

        request.state.api_user = user
        request.state.tenant_id = tenant_id

        # Everything below — endpoints, managers, background tasks — sees exactly
        # one workspace through the queryset guard in BaseManager.
        with tenant_scope(tenant_id):
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

    async def _resolve_tenant(self, request: Request, user: Any) -> tuple[Optional[int], Optional[str]]:
        """`(tenant_id, error)` — the workspace to run in, or why not.

        Never returns a workspace the caller is not a member of, whatever the
        headers say.
        """
        from app.models.managers.tenant_manager import TenantUserManager, tenants

        memberships = await TenantUserManager().web_memberships(user.id)
        if not memberships:
            return None, "User has no workspace membership"

        allowed = {m.tenant_id for m in memberships}

        raw_id = (request.headers.get(TENANT_ID_HEADER) or "").strip()
        raw_slug = (request.headers.get(TENANT_SLUG_HEADER) or "").strip()
        if not raw_id and not raw_slug:
            # Default: the first workspace the caller belongs to (same order the
            # web UI uses). Members of several workspaces select another one with
            # the headers.
            return memberships[0].tenant_id, None

        if raw_id.isdigit():
            tenant = await tenants.get(id=int(raw_id))
        else:
            tenant = await tenants.get_by_slug(raw_slug)
        if tenant is None:
            return None, "Workspace not found"
        if tenant.id not in allowed:
            return None, "User is not a member of this workspace"
        return tenant.id, None

    @staticmethod
    async def _unauthorized(scope: Scope, receive: Receive, send: Send) -> None:
        await JSONResponse(
            {"detail": "Not authenticated"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )(scope, receive, send)
