"""Admin console scope: one fixed actor, one selected workspace, one personal catalogue.

The console binds every request to its fresh signed-in actor and to a single
selected workspace membership. Platform bypass remains the owner's workspace
inspection, nothing more. Handing the whole console one undifferentiated surface
is what this middleware replaced: every operator listed every row, personal LLM
catalogs leaked across users, and the views that decrypt a provider key had to
switch create/edit off for everybody because no owner was ever bound. This
middleware replaces that bypass with the same rule the client surfaces already
use:

- **platform owner** (`superuser`) — every workspace row (`tenant_scope(bypass=True)`),
  and only their own personal catalogue: providers/models stay filtered by
  `owner_user_id` for every platform role, the owner included;
- **everyone else** — their personal rows and the one selected workspace:
  - personal catalogs are filtered by `BaseManager` on `owner_user_id`, bound by
    `catalog_execution_scope(actor.id)` — never from the request body or path;
  - workspace rows are filtered to the selected `app_tenant_id`; only that
    membership's role grants workspace actions for this request. A stronger
    membership held elsewhere lends nothing;
  - rows with no personal or workspace link stay visible only while the actor's
    platform role grants them.

`catalog_execution_scope(actor.id)` binds the personal catalog owner so
`LLMProvider.get_api_key()` has an identity to check against.

Registration order matters: this middleware is added last in `app/main.py`, which
makes it the outermost wrapper. It must run *before* `PlatformScopeMiddleware`
opens the bypass — it replaces the incoming scope instead of inheriting it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.permissions import permission_scope
from app.core.personal_catalog_context import catalog_execution_scope
from app.core.tenant_context import tenant_scope

if TYPE_CHECKING:
    from app.models import User

logger = logging.getLogger(__name__)

ADMIN_PREFIX = "/admin"

# Endpoints that must never touch data: the sign-in form, its handler, and the
# sign-out redirect. They still run scoped (fail-closed) but need no actor.
PUBLIC_ADMIN_PATHS = frozenset({"/admin/login", "/admin/logout"})


class AdminScopeMiddleware:
    """Bind the operator console to its actor instead of to the platform bypass."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if not (path == ADMIN_PREFIX or path.startswith(f"{ADMIN_PREFIX}/")):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        user = await self._session_user(request)
        # The sign-in page has no session yet; the handler still needs a tenant
        # context for its own hashing/lookup and grants nothing without an actor.
        if user is None or self._is_public(path):
            with tenant_scope(bypass=False), permission_scope(user):
                await self.app(scope, receive, send)
            return

        if user.is_superuser:
            with tenant_scope(bypass=True), catalog_execution_scope(user.id), permission_scope(user):
                await self.app(scope, receive, send)
            return

        memberships = await self._memberships(user)
        if not memberships:
            # A console operator without a workspace owns no workspace rows, so a
            # workspace page has nothing to show them. Running with no tenant is
            # not enough: the manager would raise `TenantContextError` and turn a
            # correct refusal into a 500. Refuse up front, like `/api` does.
            if self._reads_workspace_data(path):
                await self._no_workspace(scope, receive, send)
                return
            # Personal/global pages still work: their own catalogue, nothing else.
            with tenant_scope(bypass=False), catalog_execution_scope(user.id), permission_scope(user):
                await self.app(scope, receive, send)
            return

        wanted_id = request.session.get("app_tenant_id") if "session" in request.scope else None
        try:
            wanted_id = int(wanted_id) if wanted_id is not None else None
        except (TypeError, ValueError):
            wanted_id = None
        active = next((row for row in memberships if row[0] == wanted_id), memberships[0])
        active_id, active_grants = active
        if "session" in request.scope:
            request.session["app_tenant_id"] = active_id
        # One selected workspace is active per request, preventing one
        # membership role from authorizing writes to another workspace.
        with (
            tenant_scope(active_id),
            catalog_execution_scope(user.id),
            permission_scope(user, member_grants=active_grants),
        ):
            await self.app(scope, receive, send)

    @staticmethod
    async def _session_user(request: Request) -> Optional["User"]:
        """The signed-in operator, resolved exactly like the console does.

        Read from the session token only (never from a cookie body or query
        string) and loaded fresh: a revoked or re-roled account must not keep
        yesterday's scope.
        """
        token = request.session.get("token") if "session" in request.scope else None
        if not token:
            return None
        try:
            from app.models import User
            from app.services.user.auth import get_authenticated_user

            authenticated = await get_authenticated_user(token=token)
            if authenticated is None or not authenticated.is_active:
                return None
            return await User.objects.prefetch_related("role.permissions").get(id=authenticated.id, is_active=True)
        except Exception:
            logger.warning("Admin scope identity could not be resolved", exc_info=True)
            return None

    @staticmethod
    def _is_public(path: str) -> bool:
        return path.rstrip("/") in {value.rstrip("/") for value in PUBLIC_ADMIN_PATHS}

    @staticmethod
    def _reads_workspace_data(path: str) -> bool:
        """Whether the page behind `path` lists or edits a workspace-scoped model.

        `/admin/<identity>/...` maps to one registered view. A view whose model
        carries `tenant_id` cannot render without a workspace, so an actor with
        none is refused instead of being run against an empty tenant context.

        The classification is derived from the model itself, not from a list that
        would drift: every view class knows its model at import time, so this works
        even before `setup_admin()` has run. Anything that is not a *known*
        non-workspace page is treated as workspace-bound (fail closed), including
        the base views that read every table.
        """
        parts = path.split("/")
        if len(parts) < 3 or not parts[2]:
            return False
        identity = parts[2]
        from app.admin import registry as registry_module
        from app.admin import views as views_module

        candidates = [
            value
            for module in (views_module, registry_module)
            for value in vars(module).values()
            if isinstance(value, type) and getattr(value, "identity", None) == identity
        ]
        if not candidates:
            # Not one of our views: fail closed rather than let it read a table.
            return True
        model = getattr(candidates[0], "model", None)
        if model is None:
            # Base views (the table registry) read every table: the owner only.
            return True
        return bool(getattr(model, "__tenant_scoped__", False))

    @staticmethod
    async def _no_workspace(scope: Scope, receive: Receive, send: Send) -> None:
        """A signed-in operator who belongs to no workspace has no rows to show."""
        await PlainTextResponse(
            "Нет доступных рабочих пространств: эта страница показывает данные "
            "рабочего пространства, а вы не являетесь его участником.",
            status_code=403,
        )(scope, receive, send)

    @staticmethod
    async def _memberships(user) -> list[tuple[int, frozenset]]:
        """`(tenant_id, member_grants)` for every active workspace, oldest first.

        One of them becomes the request's active workspace: the one named by the
        session's `app_tenant_id` when it matches a membership, otherwise the
        oldest. The member check in `has_permission` compares against
        `current_tenant_id()`, so it applies to that workspace's role only — the
        others' roles are carried but never consulted.
        """
        from app.models.managers.tenant_manager import TenantUserManager

        manager = TenantUserManager()
        try:
            memberships = await manager.web_memberships(user.id)
            resolved: list[tuple[int, frozenset]] = []
            for membership in memberships:
                grants = await manager.member_grants(membership)
                resolved.append((membership.tenant_id, grants))
            return resolved
        except Exception:
            logger.warning("Admin scope memberships could not be read", exc_info=True)
            return []
