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

from jose import JWTError, jwt
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings
from app.core.tenant_context import tenant_scope
from app.models import User
from app.models.managers.tenant_manager import TenantUserManager, tenants

# Pages reachable without an authenticated session.
PUBLIC_PATHS = frozenset({"/app/login", "/app/register"})
# Pages reachable while logged in but without any workspace membership.
NO_WORKSPACE_PATHS = frozenset({"/app/invite", "/app/workspaces/new", "/app/logout"})


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

        if user is None and path not in PUBLIC_PATHS:
            await self._redirect(scope, receive, send, f"/app/login?next={quote(path, safe='')}")
            return

        if user is not None and not memberships and path not in NO_WORKSPACE_PATHS:
            await self._redirect(scope, receive, send, "/app/invite")
            return

        tenant_id = self._active_tenant_id(request, memberships)
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
                    request.state.workspaces.append(
                        {
                            "tenant": workspace,
                            "role": membership.role,
                            "active": membership.tenant_id == tenant_id,
                        }
                    )

        with tenant_scope(tenant_id):
            if tenant_id is not None:
                try:
                    from app.models import Notification

                    request.state.unread_notifications = await Notification.objects.filter(is_read=False).count()
                except Exception:  # noqa: BLE001 — a badge must never break a page
                    request.state.unread_notifications = 0
            await self.app(scope, receive, send)

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
            user = await User.objects.get(id=int(payload["sub"]))
        except (JWTError, KeyError, ValueError, TypeError):
            return None
        if user is None or not user.is_active:
            return None
        return user

    @staticmethod
    async def _redirect(scope: Scope, receive: Receive, send: Send, url: str) -> None:
        await RedirectResponse(url, status_code=302)(scope, receive, send)
