"""TenantUIMiddleware: session JWT → web user → tenant scope for /app/*.

Mirror of `PlatformScopeMiddleware`, but fail-closed and client-facing:

- unauthenticated requests to private pages redirect to `/app/login?next=...`;
- the active workspace is resolved from `tenant_users` web memberships
  (channel='web'), *not* from JWT claims — the token only carries `sub`;
- when a user has no workspace yet, everything but the invite/onboarding
  pages redirects to `/app/invite`;
- the request then runs inside `tenant_scope(tenant_id)`, so every
  tenant-scoped model touched by the page is filtered by `BaseManager`.

The middleware stores `web_user`, `memberships`, `tenant` and `tenant_id` on
`request.state` for the route handlers and templates.
"""

from __future__ import annotations

from urllib.parse import quote

import jwt
from jwt import PyJWTError as JWTError
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.services.tenancy import member_roles

from .nav import allows, section_of
from .perms import WebPerms

# Pages reachable without an authenticated session.
PUBLIC_PATHS = frozenset({"/app/login", "/app/register"})
# Pages reachable while logged in but without any workspace membership.
NO_WORKSPACE_PATHS = frozenset({"/app/invite", "/app/workspaces/new", "/app/logout"})
PERSONAL_ACCOUNT_PATHS = frozenset(
    {
        "/app/notifications/preferences",
        "/app/notifications/preferences/reset",
        "/app/billing/account",
        "/app/billing/account/daily-limit",
    }
)


class TenantUIMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path == "/app" or path.startswith("/app/")):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        user = await self._resolve_user(request)
        memberships = await TenantUserManager().web_memberships(user.id) if user else []

        request.state.web_user = user
        request.state.memberships = memberships
        request.state.workspaces = []
        request.state.tenant = None
        request.state.tenant_id = None
        request.state.unread_notifications = 0
        # Legacy template context stays empty. Personal connection metadata is
        # loaded by the bounded attention endpoint, not every HTML request.
        request.state.connection_alerts = []
        # Built below once the active workspace is known; always set so
        # templates can call `perms.can(...)` unconditionally.
        request.state.web_perms = WebPerms(user, memberships)

        if user is None and path not in PUBLIC_PATHS:
            # Keep the query so an invite link (`/app/invite?code=…`) survives sign-in.
            query = scope.get("query_string", b"").decode("latin-1")
            target = f"{path}?{query}" if query else path
            await self._redirect(scope, receive, send, f"/app/login?next={quote(target, safe='')}")
            return

        if (
            user is not None
            and not memberships
            and path not in NO_WORKSPACE_PATHS
            and path not in PERSONAL_ACCOUNT_PATHS
            and not (
                request.method == "POST"
                and path == "/app/settings/platforms"
                and getattr(user, "is_platform_admin", False)
            )
            and not (request.method in ("GET", "HEAD") and path in ("/app/settings", "/app/settings/"))
        ):
            await self._redirect(scope, receive, send, "/app/invite")
            return

        tenant_id = self._active_tenant_id(request, memberships)
        active = next((m for m in memberships if m.tenant_id == tenant_id), None)
        grants = await TenantUserManager().member_grants(active) if active is not None else None
        request.state.web_perms = WebPerms(user, memberships, tenant_id, grants)
        if tenant_id is not None:
            request.state.tenant = await tenants.get(id=tenant_id)
            request.state.tenant_id = tenant_id
            for membership in memberships:
                workspace = (
                    request.state.tenant
                    if membership.tenant_id == tenant_id
                    else await tenants.get(id=membership.tenant_id)
                )
                if workspace is not None:
                    role_codename: str | None = None
                    if membership.role is not None:
                        codename = membership.role.codename
                        role_codename = str(codename)
                    level = member_roles.resolve(member_roles.key_of(membership))
                    request.state.workspaces.append(
                        {
                            "tenant": workspace,
                            "role": role_codename,
                            "is_owner": bool(membership.is_owner),
                            "role_label": level.label if level is not None else "Роль не назначена",
                            "active": membership.tenant_id == tenant_id,
                        }
                    )

        # Run inside both tenant_scope (data isolation) and permission_scope
        # (rights checks). permission_scope is only active when we have a user;
        # the workspace owner passes every model right (matching `WebPerms`).
        scope_ctx = tenant_scope(tenant_id)
        perm_ctx = None
        if user is not None:
            perms = getattr(request.state, "web_perms", None)
            perm_ctx = permission_scope(user, is_owner=bool(perms is not None and perms.is_owner), member_grants=grants)

        with scope_ctx:
            if perm_ctx is not None:
                perm_ctx.__enter__()
            try:
                denied = self._denied_section(request, path)
                if denied is not None:
                    from .deps import render_section_denied

                    await render_section_denied(request, denied)(scope, receive, send)
                    return
                await self.app(scope, receive, send)
            finally:
                if perm_ctx is not None:
                    perm_ctx.__exit__(None, None, None)

    @staticmethod
    def _denied_section(request: Request, path: str):
        """The section a page view is refused for, or None.

        Only reads are gated here: every write already passes `guard_web`, which
        answers with its own message. Hiding the nav item is not the check.
        """
        if request.method not in ("GET", "HEAD") or getattr(request.state, "web_user", None) is None:
            return None
        if path in PERSONAL_ACCOUNT_PATHS:
            return None
        item = section_of(path)
        if item is None or allows(item, getattr(request.state, "web_perms", None)):
            return None
        return item

    @staticmethod
    def _active_tenant_id(request: Request, memberships) -> int | None:
        """Stored selection wins when still valid; otherwise the first workspace."""
        if not memberships:
            return None
        ids = [m.tenant_id for m in memberships]
        wanted = request.session.get("app_tenant_id")
        tenant_id = wanted if wanted in ids else ids[0]
        if request.session.get("app_tenant_id") != tenant_id:
            request.session["app_tenant_id"] = tenant_id
        return tenant_id

    @staticmethod
    async def _resolve_user(request: Request) -> User | None:
        token = request.session.get("token")
        if not token:
            return None
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            if payload.get("type") != "access":
                return None
            # `role.permissions` eager: `WebPerms.can()` reads the role's model
            # rights, and the session this row came from is already closed — a
            # lazy walk would raise DetachedInstanceError in the middle of a page.
            user = await User.objects.prefetch_related("role.permissions").get(id=int(payload["sub"]))
        except (JWTError, KeyError, ValueError, TypeError):
            return None
        if user is None or not user.is_active:
            return None
        return user

    @staticmethod
    async def _redirect(scope: Scope, receive: Receive, send: Send, url: str) -> None:
        await RedirectResponse(url, status_code=302)(scope, receive, send)
