"""Trusted personal catalog identity; workspace ownership never implies key access."""

from contextlib import contextmanager
from contextvars import ContextVar

_DENIED_CATALOG = object()
_catalog_owner = ContextVar("personal_catalog_owner", default=None)


def current_catalog_owner_id():
    bound = _catalog_owner.get()
    if bound is _DENIED_CATALOG:
        raise PermissionError("Job has no authorized personal catalog owner")
    if bound is not None:
        return bound
    from app.core.permissions import get_current_user
    from app.core.tenant_context import is_bypass

    actor = get_current_user()
    if actor is not None and actor.is_active and type(actor.id) is int and actor.id > 0:
        return actor.id
    # A script or an offline fixture that already runs as the platform owner
    # writes into the bootstrap catalogue, exactly like the owner's console
    # request does (`AdminScopeMiddleware` binds the owner id there). An
    # interactive request never reaches this branch: the console and the API
    # always bind an actor first, so the fallback cannot be inherited by a
    # caller who merely happens to hold the bypass.
    if is_bypass():
        return 2
    raise PermissionError("An active personal catalog owner is required")


@contextmanager
def owner_of_catalog(user_id):
    """Explicit owner binding for scripts/fixtures that act on behalf of a user.

    Same guarantee as `catalog_execution_scope` (a positive id, never a value
    taken from a request), under a name that says the caller *is* that user —
    tests and maintenance scripts have no ambient `permission_scope` actor.
    """
    with catalog_execution_scope(user_id):
        yield


@contextmanager
def catalog_execution_scope(user_id):
    """Internal worker boundary only; never bind from request/payload owner IDs."""
    if type(user_id) is not int or user_id <= 0:
        raise PermissionError("Invalid personal catalog owner")
    token = _catalog_owner.set(user_id)
    try:
        yield
    finally:
        _catalog_owner.reset(token)


def assert_catalog_model(model):
    owner = current_catalog_owner_id()
    provider = getattr(model, "provider", None)
    if (
        getattr(model, "owner_user_id", None) != owner
        or provider is None
        or getattr(provider, "owner_user_id", None) != owner
    ):
        raise PermissionError("Model is unavailable in this personal catalog")


@contextmanager
def without_catalog_access():
    """Pure collection must not inherit an HTTP actor or parent worker catalog."""
    token = _catalog_owner.set(_DENIED_CATALOG)
    try:
        yield
    finally:
        _catalog_owner.reset(token)
