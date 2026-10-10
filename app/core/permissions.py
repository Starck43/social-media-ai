"""Centralized permission enforcement for non-HTTP surfaces.

The HTTP surfaces each answer "may this caller do this" through
`User.model_permissions()` / `User.has_perm_for()` (`app/api/deps.py`,
`app/admin/authorization.py`, `app/web/perms.py`). The chat agent, managers
and job code run without a request, so they need the same answer without an
HTTP layer — that is what this module provides.

One predicate: everything here delegates to `User.has_perm_for()`
(structured `permissions.model_type_id` + `action_type`, never the stored
codename, which is not reliably parseable). The current user travels in a
`ContextVar` set by `permission_scope()`. Only explicit
`tenant_scope(bypass=True)` skips valid rights checks. A `None` user is denied. Trusted bookkeeping receives a narrow, tenant-bound
service grant at the write site, not an implicit anonymous identity.
"""

from __future__ import annotations

import functools
import inspect
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any, Callable, Iterator, Optional, Union

from app.core.tenant_context import current_tenant_id, is_bypass
from app.types import ActionType, UserRoleType

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
]

_operator_grant: ContextVar[bool] = ContextVar("operator_permission_grant", default=False)
_current_user: ContextVar[Optional["User"]] = ContextVar("current_user", default=None)
# Ownership configures workspace resources, never the global fleet or queue.
WORKSPACE_OWNER_MODELS = frozenset({"source", "agenttask", "agentscenario"})
_workspace_owner: ContextVar[Optional[int]] = ContextVar("workspace_owner", default=None)
_service_grant: ContextVar[Optional[tuple[int, str, ActionType]]] = ContextVar("service_grant", default=None)


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


def _resolve_role(role: Union[UserRoleType, str]) -> Optional[UserRoleType]:
    """Accept a `UserRoleType` or its name (`"admin"`, `"ADMIN"`)."""
    if isinstance(role, UserRoleType):
        return role
    if not isinstance(role, str):
        return None
    token = role.strip().upper()
    if not token:
        return None
    return UserRoleType.get_by_name(token)


def _role_name(codename: Any) -> str:
    """Normalise a role codename that may be an enum member or its stored name."""
    return codename.name if hasattr(codename, "name") else str(codename)


def has_permission(user: Optional["User"], model_name: str, action: Union[ActionType, str]) -> bool:
    """True when `user` may perform `action` on `model_name`.

    `model_name` is the `model_types.model_name` value (`source`,
    `agenttask`, ...); the check delegates to `User.has_perm_for()`.
    Only explicit operator bypass skips checks. Anonymous callers are denied;
    workspace ownership is tenant-bound and limited to configuration models.
    """
    resolved = _resolve_action(action)
    if resolved is None or not isinstance(model_name, str) or not model_name.strip():
        return False
    model_name = model_name.strip().lower()
    if is_bypass() or _operator_grant.get():
        return True
    if _service_grant.get() == (current_tenant_id(), model_name, resolved):
        return True
    if user is None or not getattr(user, "is_active", True):
        return False
    if (
        _workspace_owner.get() is not None
        and _workspace_owner.get() == current_tenant_id()
        and user is get_current_user()
        and model_name in WORKSPACE_OWNER_MODELS
    ):
        return True
    try:
        return bool(user.has_perm_for(model_name, resolved))
    except Exception:  # noqa: BLE001 - a rights check must never raise
        return False


def has_permission_by_codename(user: Optional["User"], codename: str) -> bool:
    """True when `user` may perform the dotted `"model.action"` codename.

    The `tool(required_permission=...)` values and the confirmation flow store
    the codename shape (`"agenttask.create"`); `has_permission` needs model and
    action split. Malformed input is denied even for owners/operators.
    """
    if not isinstance(codename, str):
        return False
    model_name, _, action = (codename or "").rpartition(".")
    if not model_name or not action or "." in model_name:
        return False
    return has_permission(user, model_name, action)


def has_role(user: Optional["User"], role: Union[UserRoleType, str]) -> bool:
    """True when `user` holds `role` on the platform ladder.

    Only explicit operator bypass skips a valid role check; anonymous callers
    and workspace ownership never grant a platform role.
    """
    resolved = _resolve_role(role)
    if resolved is None:
        return False
    if is_bypass() or _operator_grant.get():
        return True
    if user is None or not getattr(user, "is_active", True):
        return False
    try:
        if user._is_superuser_role():
            return True
        codename = user.role.codename if user.role else None
        if codename is None:
            return False
        return _role_name(codename) == resolved.name
    except Exception:  # noqa: BLE001 - a rights check must never raise
        return False


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


def require_role(role: Union[UserRoleType, str]) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
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
def permission_scope(user: Optional["User"], *, is_owner: bool = False) -> Iterator[Optional["User"]]:
    """Run a block as `user`; restores the previous user on exit.

    `is_owner=True` grants only workspace configuration rights in the tenant
    active at entry. Nested interactive scopes clear inherited service grants.
    """
    token = set_current_user(user)
    owner_token = _workspace_owner.set(current_tenant_id() if is_owner else None)
    service_token = _service_grant.set(None)
    operator_token = _operator_grant.set(False)
    try:
        yield user
    finally:
        _current_user.reset(token)
        _workspace_owner.reset(owner_token)
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
