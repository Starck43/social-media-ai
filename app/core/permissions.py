"""Shared interactive workspace policy and explicit trusted-service contexts.

Workspace membership grants never fall back to platform authority. Operator
HTTP requests combine a global data scope with fresh platform action checks;
unbound bypass contexts are reserved for guarded CLI, admin and worker entrypoints.
"""

from __future__ import annotations

import functools
import inspect
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any, Callable, Iterator, Optional, Union

from app.core.access_catalog import resolve_permission_key, resource_for, workspace_grants
from app.core.tenant_context import current_tenant_id, current_tenant_ids, is_bypass
from app.types import ActionType, PlatformRoleType

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models import User

__all__ = [
    "PermissionDeniedError",
    "get_current_user",
    "set_current_user",
    "has_permission",
    "has_permission_by_codename",
    "has_role",
    "require_permission",
    "require_role",
    "permission_scope",
    "service_permission_scope",
    "operator_permission_scope",
    "WORKSPACE_OWNER_MODELS",
    "workspace_models",
    "member_decision",
]

_platform_request: ContextVar[bool] = ContextVar("platform_http_request", default=False)
_operator_grant: ContextVar[bool] = ContextVar("operator_permission_grant", default=False)
_current_user: ContextVar[Optional["User"]] = ContextVar("current_user", default=None)
# Ownership configures workspace resources, never the global fleet or queue.
WORKSPACE_OWNER_MODELS = frozenset({"source", "agenttask", "agentscenario"})
_workspace_owner: ContextVar[Optional[int]] = ContextVar("workspace_owner", default=None)
_service_grant: ContextVar[Optional[tuple[int, str, ActionType]]] = ContextVar("service_grant", default=None)
# A non-owner member's workspace rights: (tenant_id, user_id, {(model, action)}).
_member_grants: ContextVar[Optional[tuple[int, int, frozenset]]] = ContextVar("member_grants", default=None)


def workspace_models() -> frozenset[str]:
    from app.core.access_catalog import RESOURCES

    return frozenset(r.model_name for r in RESOURCES if r.workspace)


class PermissionDeniedError(PermissionError):
    """A rights check refused the current user.

    Message format matches the API layer (`app/api/deps.py`), so a refusal
    reads the same whatever surface produced it:
    `Missing permission: source.delete`.
    """


def get_current_user() -> Optional["User"]:
    """The user `permission_scope()` set for this task, or None outside one."""
    return _current_user.get()


def set_current_user(user: Optional["User"]) -> Token:
    """Set the current user; returns a token to restore the previous one."""
    return _current_user.set(user)


def _resolve_action(action: Union[ActionType, str]) -> Optional[ActionType]:
    """Accept an `ActionType` or its db value / name (`"update"`, `"UPDATE"`)."""
    if isinstance(action, ActionType):
        return action
    if not isinstance(action, str):
        return None
    token = action.strip().lower()
    if not token:
        return None
    return ActionType.get_by_value(token) or ActionType.get_by_name(action.strip().upper())


def _resolve_role(role):
    try:
        return PlatformRoleType(str(role).lower())
    except (ValueError, TypeError):
        return None


def has_permission(user, model_name, action) -> bool:
    resource = resource_for(model_name)
    resolved = _resolve_action(action)
    if resource is None or resolved not in resource.actions:
        return False
    if is_bypass():
        current = get_current_user()
        if current is None:
            return not _platform_request.get()  # Only non-HTTP trusted entrypoints may omit identity.
        return bool(
            user is not None
            and getattr(user, "id", None) == getattr(current, "id", None)
            and user.has_admin_access()
            and user.has_perm_for(resource.model_name, resolved)
        )
    if _operator_grant.get():
        return True
    if _service_grant.get() == (current_tenant_id(), resource.model_name, resolved):
        return True
    if user is None or not getattr(user, "is_active", False):
        return False
    current = get_current_user()
    if resource.model_name in {"llmmodel", "llmprovider"}:
        return bool(current is not None and current.id == user.id and current.is_active)
    decision = member_decision(user, resource.model_name, resolved)
    if decision is not None:
        return decision
    # Interactive workspace requests can never fall back to platform grants.
    if current_tenant_id() is not None or get_current_user() is not None:
        return False
    try:
        return bool(user.has_perm_for(resource.model_name, resolved))
    except Exception:
        return False


def member_decision(user, model_name, action):
    resource = resource_for(model_name)
    member = _member_grants.get()
    current = get_current_user()
    if current is None:
        return None
    # The role belongs to the workspace the *write* concerns. A console operator
    # reads several workspaces at once (`current_tenant_ids()`); the active one
    # decides, so a right from one workspace can never re-authorize another.
    tenant_id = (member[0] if member is not None else None) or current_tenant_id()
    if (
        resource is None
        or not resource.workspace
        or member is None
        or user is None
        or member[0] != current_tenant_id()
        or tuple(member) != (tenant_id, getattr(current, "id", None), member[2])
        or member[1] != getattr(current, "id", None)
        or member[1] != getattr(user, "id", None)
        or current_tenant_id() not in current_tenant_ids()
    ):
        return False
    return action in resource.actions and (resource.model_name, action) in member[2]


def has_permission_by_codename(user, codename) -> bool:
    pair = resolve_permission_key(codename)
    return bool(pair and has_permission(user, pair[0].model_name, pair[1]))


def has_role(user, role) -> bool:
    resolved = _resolve_role(role)
    if resolved is None:
        return False
    current = get_current_user()
    if (is_bypass() and current is None and not _platform_request.get()) or _operator_grant.get():
        return True
    if user is None or not getattr(user, "is_active", False) or (_platform_request.get() and current is None):
        return False
    if current is not None and getattr(current, "id", None) != getattr(user, "id", None):
        return False
    return bool(user.role and user.role.codename == resolved)


def _scope_user(kwargs: dict) -> Optional["User"]:
    """Explicit `current_user=` kwarg wins, otherwise the ambient scope."""
    if "current_user" in kwargs:
        return kwargs["current_user"]
    return get_current_user()


def require_permission(
    model_name: str, action: Union[ActionType, str]
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Gate a function on a model right; raises `PermissionDeniedError`.

    Reads the caller from `permission_scope()` (or an explicit
    `current_user=` kwarg). Works on sync and async callables.
    """
    resolved = _resolve_action(action)
    if resolved is None:
        raise ValueError(f"Unknown action: {action!r}")
    message = f"Missing permission: {model_name}.{resolved.db_value}"

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                if not has_permission(_scope_user(kwargs), model_name, resolved):
                    raise PermissionDeniedError(message)
                return await fn(*args, **kwargs)

            async_wrapper.__permission_gate__ = (model_name, resolved.db_value)  # type: ignore[attr-defined]
            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            if not has_permission(_scope_user(kwargs), model_name, resolved):
                raise PermissionDeniedError(message)
            return fn(*args, **kwargs)

        sync_wrapper.__permission_gate__ = (model_name, resolved.db_value)  # type: ignore[attr-defined]
        return sync_wrapper

    return decorator


def require_role(role: Union[PlatformRoleType, str]) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Gate a function on a platform role; raises `PermissionDeniedError`."""
    resolved = _resolve_role(role)
    if resolved is None:
        raise ValueError(f"Unknown role: {role!r}")
    message = f"Requires role {resolved.name} or higher"

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                if not has_role(_scope_user(kwargs), role):
                    raise PermissionDeniedError(message)
                return await fn(*args, **kwargs)

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            if not has_role(_scope_user(kwargs), role):
                raise PermissionDeniedError(message)
            return fn(*args, **kwargs)

        return sync_wrapper

    return decorator


@contextmanager
def permission_scope(
    user: Optional["User"], *, is_owner: bool = False, member_grants: Optional[frozenset] = None
) -> Iterator[Optional["User"]]:
    """Run a block as `user`; restores the previous user on exit.

    `is_owner=True` grants only workspace configuration rights in the tenant
    active at entry. `member_grants` — the `(model, action)` pairs of a non-owner
    member's workspace role — replaces the platform role for workspace models in
    that tenant (an owner ignores it). Nested interactive scopes clear inherited
    service grants.
    """
    tenant_id = current_tenant_id()
    member = None
    if member_grants is None and is_owner:
        member_grants = workspace_grants("owner")
    if member_grants is not None and tenant_id is not None and user is not None:
        member = (tenant_id, getattr(user, "id", None), frozenset(member_grants))
    token = set_current_user(user)
    owner_token = _workspace_owner.set(tenant_id if is_owner else None)
    member_token = _member_grants.set(member)
    service_token = _service_grant.set(None)
    operator_token = _operator_grant.set(False)
    try:
        yield user
    finally:
        _current_user.reset(token)
        _workspace_owner.reset(owner_token)
        _member_grants.reset(member_token)
        _service_grant.reset(service_token)
        _operator_grant.reset(operator_token)


@contextmanager
def service_permission_scope(model_name: str, action: Union[ActionType, str]) -> Iterator[None]:
    """Delegate one trusted bookkeeping right in the current tenant.

    Internal write sites only: never wrap tool dispatch or accept a model/action
    from external input. Does not widen tenant data access or grant role rights.
    """
    tenant_id = current_tenant_id()
    resolved = _resolve_action(action)
    if tenant_id is None or model_name not in WORKSPACE_OWNER_MODELS or resolved is None:
        raise ValueError("Service permission requires a tenant and a known workspace right")
    token = _service_grant.set((tenant_id, model_name, resolved))
    try:
        yield
    finally:
        _service_grant.reset(token)


@contextmanager
def operator_permission_scope() -> Iterator[None]:
    """Trusted CLI entrypoint authority, independent of tenant data narrowing.

    Never wrap an HTTP/chat request with this scope. Interactive permission_scope
    clears the grant; tenant_scope alone may safely narrow operator data access.
    """
    token = _operator_grant.set(True)
    try:
        yield
    finally:
        _operator_grant.reset(token)


@contextmanager
def platform_request_scope():
    """Mark an operator HTTP dispatch independently from its ambient identity."""
    token = _platform_request.set(True)
    try:
        yield
    finally:
        _platform_request.reset(token)
