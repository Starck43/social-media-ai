"""Role-based access control for the sqladmin operator console.

`/admin/*` is the operator console, and it is gated the way Django gates its
`ModelAdmin` — the rights of the signed-in operator are the rights of their
platform role, and every model in the console is treated the same way:

* a model the role grants nothing on is not in the menu at all
  (`ModelView.is_accessible`), and its routes answer 403;
* `list` / `details` / `create` / `edit` / `delete` / `export` / `import` map
  one-to-one onto `ActionType`, so hiding a button also blocks the route behind
  it — sqladmin asks the backend about each of them (`check_can_*`,
  `_can_delete_any`, the custom-action buttons);
* as in Django, `has_view_permission` is implied by any other right: an
  operator who may only *create* a record still has to open the change list to
  reach the form, so `create`/`update`/`delete`/`export` grant `list` + `details`;
* a custom `@action` is not model CRUD, so each one declares the right it needs
  in `BaseAdmin.action_permissions` (running a task, re-analysing, sending a
  notification are `update`; probing an LLM provider is `configure`).

Only active platform operators and administrators may enter. Operator grants
come from `role_permission` -> `permissions.model_type_id` + `action_type`,
read through `User.model_permissions()`. A platform administrator bypasses
individual grants; workspace roles never grant console access.

sqladmin asks `has_permission` many times while rendering one page (once per
row), so it must not do I/O. Everything is read once per request in
`get_grants()` — which `load()` calls right after authentication — and stored as
a set of `(identity, action)` pairs that later calls only match against.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any, Optional

from fastapi import HTTPException, status
from sqladmin.authorization import (
    WILDCARD,
    Action,
    GrantsAuthorizationBackend,
    custom_action,
)
from sqladmin.models import ModelView
from starlette.requests import Request

from app.models import User
from app.types import ActionType

logger = logging.getLogger(__name__)

# The console request currently being served. sqladmin hands `scaffold_form`
# the rules but not the request, so the actor it needs is published here by
# `AdminAuthorizationBackend.load()`. A context var, not an attribute: two
# operators can render forms at the same time and each must see their own.
_console_request: ContextVar[Optional[Request]] = ContextVar("console_request", default=None)


def bind_console_request(request: Request) -> None:
    """Publish `request` to console code that sqladmin calls without it."""
    _console_request.set(request)


def bound_console_request() -> Optional[Request]:
    """The request bound by `load()`, or None outside a console request."""
    return _console_request.get()


# sqladmin action -> the `ActionType` a role must carry for it. `Action` is a
# `str` enum, so these keys match the plain strings sqladmin passes in.
BUILTIN_ACTION_TYPES: dict[str, ActionType] = {
    Action.LIST: ActionType.VIEW,
    Action.DETAILS: ActionType.VIEW,
    Action.CREATE: ActionType.CREATE,
    Action.EDIT: ActionType.UPDATE,
    Action.DELETE: ActionType.DELETE,
    Action.EXPORT: ActionType.EXPORT,
    # An import writes rows exactly like filling the create form by hand.
    Action.IMPORT: ActionType.CREATE,
}

# What a custom `@action` needs when the view does not say otherwise. The
# unlisted ones are mutations (run a task, toggle a scenario, send a
# notification); a pure read declares VIEW explicitly.
DEFAULT_ACTION_TYPE = ActionType.UPDATE

# Marks "load() has not run for this request" — the deny-rather-than-fall-open
# default of `GrantsAuthorizationBackend` uses the same idea for the grants.
_MISSING = object()


class AdminAuthorizationBackend(GrantsAuthorizationBackend):
    """Gate the console by the model permissions of the operator's role."""

    def __init__(self) -> None:
        self._admin: Any | None = None
        # identity -> model name in `model_types`, and identity -> {slug: right}
        # for the view's custom actions. Rebuilt when the view list changes.
        self._signature: tuple[str, ...] = ()
        self._model_by_identity: dict[str, str] = {}
        self._custom_rights: dict[str, dict[str, ActionType]] = {}

    def setup(self, admin: Any) -> None:
        """Keep the `Admin` so the registered views can be read on first use.

        Called by `Admin.__init__`, which happens *before* `admin.add_view(...)`
        in `app.admin.setup.setup_admin` — hence the lazy catalogue in `_catalog`.
        """
        self._admin = admin

    # --- per-request state -------------------------------------------------

    async def load(self, request: Request) -> None:
        """Prepare the request, and publish it to code sqladmin calls later.

        `scaffold_form` gets no request argument, and `BaseAdmin._form_request`
        only covers the routes of one view — so the request is bound here, once,
        where sqladmin already guarantees "authentication done, nothing rendered
        yet". The context var is task-local: two operators browsing at the same
        time each see their own.
        """
        bind_console_request(request)
        await super().load(request)

    async def get_grants(self, request: Request) -> set[tuple[str, str]]:
        """The `(identity, action)` pairs the signed-in operator may use.

        Empty for an anonymous/non-operator request, the wildcard pair for the
        platform owner, and the role's rights translated into console actions
        otherwise. An `admin` is not a wildcard holder: its console is exactly
        what `role_permission` grants the `admin` role, which is what lets the
        owner narrow one admin's reach without touching the owner's own.
        """
        user = await self.resolve_user(request)
        if user is None or not user.has_admin_access():
            return set()
        if user.is_superuser:
            return {(WILDCARD, WILDCARD)}

        model_by_identity, custom_rights = self._catalog(request)
        grants: set[tuple[str, str]] = set()
        for identity, model_name in model_by_identity.items():
            granted = user.model_permissions(model_name)
            # Inside a selected workspace, platform console grants are only the
            # outer ceiling. The active membership role must also grant the
            # action; otherwise an ADMIN with platform-wide CRUD could edit a
            # workspace where they are only a viewer.
            from app.core.access_catalog import resource_for
            from app.core.permissions import has_permission
            from app.core.tenant_context import current_tenant_id

            resource = resource_for(model_name)
            if current_tenant_id() is not None and resource is not None and resource.workspace:
                granted = {action for action in granted if has_permission(user, model_name, action)}
            if not granted:
                continue

            for action_name, action_type in BUILTIN_ACTION_TYPES.items():
                if action_type in granted:
                    grants.add((identity, action_name))

            # Django: add/change/delete imply the change list.
            if granted - {ActionType.VIEW}:
                grants.add((identity, Action.LIST))
                grants.add((identity, Action.DETAILS))

            for slug, action_type in custom_rights.get(identity, {}).items():
                if action_type in granted:
                    grants.add((identity, custom_action(slug)))

        return grants

    async def resolve_user(self, request: Request) -> Optional[User]:
        """The operator behind this request, role and permissions loaded.

        Cached on `request.state` because the console also reads it from the
        view overrides (`TenantAdmin.is_accessible`) and the templates, not only
        from this backend.
        """
        cached = getattr(request.state, "admin_user", _MISSING)
        if cached is not _MISSING:
            return cached

        user: Optional[User] = None
        token = request.session.get("token")
        if token:
            try:
                from app.services.user.auth import get_authenticated_user

                authenticated = await get_authenticated_user(token=token)
                if authenticated is not None and authenticated.is_active:
                    user = await User.objects.prefetch_related("role.permissions").get(id=authenticated.id)
                    if user is not None and (not user.is_active or not user.has_admin_access()):
                        user = None
            except Exception:
                # Revoked/invalid identity or unavailable DB must grant nothing.
                logger.warning("Admin identity could not be resolved", exc_info=True)

        request.state.admin_user = user
        return user

    # --- view catalogue ----------------------------------------------------

    def _catalog(self, request: Request) -> tuple[dict[str, str], dict[str, dict[str, ActionType]]]:
        """`identity -> model_types.model_name` for the registered model views.

        sqladmin identifies a view by the slugified model class name
        (`AgentTask` -> `agent-task`) while `model_types` stores the lowercased
        class name (`agenttask`), so the two differ only by the dashes. Reading
        the mapping off the registered views instead of a hand-written table
        means a new admin view is gated the moment it is added — there is nothing
        to keep in sync — and a view whose model is unknown simply gets no rights.
        """
        admin = self._admin or getattr(request.app.state, "admin", None)
        views = [view for view in (getattr(admin, "_views", None) or []) if getattr(view, "model", None)]
        signature = tuple(view.identity for view in views)
        if signature != self._signature:
            self._signature = signature
            self._model_by_identity = {view.identity: view.model.__name__.lower() for view in views}
            self._custom_rights = {view.identity: _custom_action_types(view) for view in views}
        return self._model_by_identity, self._custom_rights


def _custom_action_types(view: ModelView) -> dict[str, ActionType]:
    """`{slug: ActionType}` for the `@action`s declared on a view.

    The slugs come from the `action` decorator (`func._slug`), not from the
    `add_in_list` / `add_in_detail` flags: an action reachable only from the
    details page still has to pass the check on its endpoint.

    The decorator slugifies the function name (`mark_read` -> `mark-read`), so a
    key written in the function's spelling would silently fall back to the
    default right — a warning here is what turns that into a visible mistake.
    """
    declared = getattr(view, "action_permissions", None) or {}
    actions: dict[str, ActionType] = {}
    for name in dir(type(view)):
        slug = getattr(getattr(view, name, None), "_slug", None)
        if slug:
            actions[slug] = declared.get(slug, DEFAULT_ACTION_TYPE)

    unknown = set(declared) - set(actions)
    if unknown:
        logger.warning(
            "%s.action_permissions names actions the view does not declare: %s (declared: %s)",
            type(view).__name__,
            sorted(unknown),
            sorted(actions),
        )
    return actions


def _console_backend(request: Request) -> AdminAuthorizationBackend:
    """The backend configured on the running `Admin` (the one that loaded it)."""
    backend = getattr(getattr(request.app.state, "admin", None), "authorization_backend", None)
    if not isinstance(backend, AdminAuthorizationBackend):
        # No console on this app (`ADMIN_ENABLED` off): nothing is allowed.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Консоль администратора отключена")
    return backend


async def require_admin_perm(request: Request, model_name: str, action: ActionType) -> User:
    """Gate a console route that lives outside the sqladmin view tree.

    `/dashboard` and the password pages are plain FastAPI routes in
    `app.admin.endpoints`, so sqladmin never asks the backend about them. They
    go through the same `User.model_permissions()` the rest of the console uses,
    which keeps one answer to "may this operator do this" instead of two.
    """
    backend = _console_backend(request)
    # Plain FastAPI routes do not run SQLAdmin's login_required wrapper.
    # Load the same request-bound grants before rendering its shared menu.
    await backend.load(request)
    user = await backend.resolve_user(request)
    if user is None or not user.has_admin_access() or not user.has_perm_for(model_name, action):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing permission: {model_name}.{action.db_value}",
        )
    return user
