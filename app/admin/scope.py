"""Console scope middleware: one actor, one selected workspace, one personal catalogue.

`PlatformScopeMiddleware` opens a platform-wide tenant bypass on infrastructure
prefixes, and `/admin` used to sit in that list. That let every signed-in
operator read every workspace's rows.

This middleware replaces that bypass for `/admin/*` and `/dashboard/*`:

- the platform owner (`user.is_superuser`) keeps global workspace inspection,
  with their own personal catalogue bound (`catalog_execution_scope(user.id)`);
- every other console actor runs inside one active workspace — the session's
  `app_tenant_id` when it names a workspace they belong to, otherwise their
  oldest active membership — and under *that* membership's workspace role;
- an actor with no workspace membership is refused on workspace-data pages and
  allowed through with no workspace on personal/platform pages;
- every console actor gets their own personal catalog owner so
  `LLMProvider.get_api_key()` has an identity to check against.

Registration order matters: this middleware is added last in `app/main.py`, which
makes it the outermost wrapper. It must run *before* `PlatformScopeMiddleware`
opens the bypass — it replaces the incoming scope instead of inheriting it.
"""

from __future__ import annotations

import functools
import logging
from typing import TYPE_CHECKING, Optional

from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.admin.identity import resolve_operator_identity
from app.admin.session import row_criteria_scope
from app.core.access_catalog import workspace_grants
from app.core.permissions import permission_scope
from app.core.personal_catalog_context import catalog_execution_scope
from app.core.tenant_context import tenant_scope

if TYPE_CHECKING:
    from app.models import User

logger = logging.getLogger(__name__)

ADMIN_PREFIX = "/admin"
DASHBOARD_PREFIX = "/dashboard"

# Endpoints that must never touch data: the sign-in form, its handler, and the
# sign-out redirect. They still run scoped (fail-closed) but need no actor.
PUBLIC_ADMIN_PATHS = frozenset({"/admin/login", "/admin/logout"})


@functools.lru_cache(maxsize=1)
def _workspace_view_by_identity() -> dict[str, bool]:
    """Map each registered console view identity to whether it reads workspace rows."""
    from app.admin import registry as registry_module
    from app.admin import views as views_module

    mapping: dict[str, bool] = {}
    for module in (views_module, registry_module):
        for value in vars(module).values():
            if not isinstance(value, type):
                continue
            identity = getattr(value, "identity", None)
            if not isinstance(identity, str) or not identity or identity in mapping:
                continue
            model = getattr(value, "model", None)
            # Base views (the table registry) read every table: the owner only.
            mapping[identity] = True if model is None else bool(getattr(model, "__tenant_scoped__", False))
    return mapping


def _inline_membership_grants(membership) -> frozenset | None:
    """Resolve grants from a preloaded `membership.role` without a database query."""
    if membership is None or not getattr(membership, "is_active", True):
        return frozenset()
    role = getattr(membership, "role", None)
    if role is not None:
        codename = getattr(role, "codename", None)
        return workspace_grants(str(codename)) if codename is not None else frozenset()
    if hasattr(membership, "role_id") and getattr(membership, "role_id", None) is None:
        return frozenset()
    return None


class AdminScopeMiddleware:
    """Bind the operator console to its actor instead of to the platform bypass."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not any(path == prefix or path.startswith(f"{prefix}/") for prefix in (ADMIN_PREFIX, DASHBOARD_PREFIX)):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        user = await self._session_user(request)

        # The sign-in page has no session yet; the handler still needs a tenant
        # context for its own hashing/lookup and grants nothing without an actor.
        if user is None or self._is_public(path):
            with row_criteria_scope(), tenant_scope(bypass=False), permission_scope(user):
                await self.app(scope, receive, send)
            return

        if user.is_superuser:
            with (
                row_criteria_scope(),
                tenant_scope(bypass=True),
                catalog_execution_scope(user.id),
                permission_scope(user),
            ):
                await self.app(scope, receive, send)
            return

        wanted_id = request.session.get("app_tenant_id") if "session" in request.scope else None
        try:
            wanted_id = int(wanted_id) if wanted_id is not None else None
        except (TypeError, ValueError):
            wanted_id = None

        memberships = await self._memberships(user, wanted_id=wanted_id, only_active=True)
        if not memberships:
            # A console operator without a workspace owns no workspace rows, so a
            # workspace page has nothing to show them. Running with no tenant is
            # not enough: the manager would raise `TenantContextError` and turn a
            # correct refusal into a 500. Refuse up front, like `/api` does.
            if self._reads_workspace_data(path):
                await self._no_workspace(scope, receive, send)
                return
            # Personal/global pages still work: their own catalogue, nothing else.
            with (
                row_criteria_scope(),
                tenant_scope(bypass=False),
                catalog_execution_scope(user.id),
                permission_scope(user),
            ):
                await self.app(scope, receive, send)
            return

        active = next((row for row in memberships if row[0] == wanted_id), memberships[0])
        active_id, active_grants = active
        if "session" in request.scope:
            request.session["app_tenant_id"] = active_id

        # One selected workspace is active per request, preventing one
        # membership role from authorizing writes to another workspace.
        with (
            row_criteria_scope(),
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
            user = await resolve_operator_identity(token)
            if user is None or not user.is_active:
                return None
            return user
        except Exception:
            logger.warning("Admin scope identity could not be resolved", exc_info=True)
            return None

    @staticmethod
    def _is_public(path: str) -> bool:
        return path.rstrip("/") in {value.rstrip("/") for value in PUBLIC_ADMIN_PATHS}

    @staticmethod
    @functools.lru_cache(maxsize=256)
    def _reads_workspace_data(path: str) -> bool:
        """Whether the page behind `path` lists or edits a workspace-scoped model.

        `/admin/<identity>/...` maps to one registered view. A view whose model
        carries `tenant_id` cannot render without a workspace, so an actor with
        none is refused instead of being run against an empty tenant context.

        The classification is derived from the model itself and cached per view
        identity: every view class knows its model at import time, so this works
        even before `setup_admin()` has run. Anything that is not a *known*
        non-workspace page is treated as workspace-bound (fail closed), including
        the base views that read every table.
        """
        if path == DASHBOARD_PREFIX or path.startswith(f"{DASHBOARD_PREFIX}/"):
            return True
        parts = path.split("/")
        if len(parts) < 3 or not parts[2]:
            return False
        identity = parts[2]
        return _workspace_view_by_identity().get(identity, True)

    @staticmethod
    async def _no_workspace(scope: Scope, receive: Receive, send: Send) -> None:
        """A signed-in operator who belongs to no workspace has no rows to show."""
        await PlainTextResponse(
            "Нет доступных рабочих пространств: эта страница показывает данные "
            "рабочего пространства, а вы не являетесь его участником.",
            status_code=403,
        )(scope, receive, send)

    @staticmethod
    async def _memberships(
        user, *, wanted_id: int | None = None, only_active: bool = False
    ) -> list[tuple[int, frozenset]]:
        """`(tenant_id, member_grants)` for every active workspace, oldest first.

        `TenantUser.role` is eager-loaded (`lazy="selectin"`), so grants come
        straight from the loaded role without a per-membership query. When roles
        are not preloaded on detached rows, missing `role_id`s are fetched in one
        batch; when `member_grants` is overridden by a test double, only the
        selected active membership is queried when `wanted_id` is supplied.
        """
        from app.models.managers.tenant_manager import TenantUserManager

        manager = TenantUserManager()
        try:
            memberships = await manager.web_memberships(user.id)
            if not memberships:
                return []

            active_membership = next((m for m in memberships if m.tenant_id == wanted_id), memberships[0])
            default_member_grants = getattr(
                TenantUserManager.member_grants, "__func__", TenantUserManager.member_grants
            )
            current_member_grants = getattr(type(manager).member_grants, "__func__", type(manager).member_grants)
            custom_member_grants = current_member_grants is not default_member_grants

            batched_roles: dict[int, frozenset] = {}
            if not custom_member_grants:
                missing_role_ids = {
                    m.role_id
                    for m in memberships
                    if _inline_membership_grants(m) is None and getattr(m, "role_id", None) is not None
                }
                if missing_role_ids:
                    from app.models.tenancy.workspace_role import WorkspaceRole

                    roles = await WorkspaceRole.objects.filter(WorkspaceRole.id.in_(missing_role_ids))
                    batched_roles = {
                        role.id: workspace_grants(str(role.codename))
                        for role in roles
                        if getattr(role, "codename", None) is not None
                    }

            resolved: list[tuple[int, frozenset]] = []
            for membership in memberships:
                inline = _inline_membership_grants(membership)
                if inline is not None:
                    resolved.append((membership.tenant_id, inline))
                elif not custom_member_grants and getattr(membership, "role_id", None) is not None:
                    resolved.append((membership.tenant_id, batched_roles.get(membership.role_id, frozenset())))
                elif (only_active or wanted_id is not None) and membership is not active_membership:
                    resolved.append((membership.tenant_id, frozenset()))
                else:
                    grants = await manager.member_grants(membership)
                    resolved.append((membership.tenant_id, grants))
            return resolved
        except Exception:
            logger.warning("Admin scope memberships could not be read", exc_info=True)
            return []
