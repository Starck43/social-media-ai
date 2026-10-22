"""Trusted personal catalog identity; workspace ownership never implies key access."""
from contextlib import contextmanager
from contextvars import ContextVar

_catalog_owner = ContextVar("personal_catalog_owner", default=None)


def current_catalog_owner_id():
    bound = _catalog_owner.get()
    if bound is not None:
        return bound
    from app.core.permissions import get_current_user
    actor = get_current_user()
    if actor is not None and actor.is_active and type(actor.id) is int:
        return actor.id
    raise PermissionError("An active personal catalog owner is required")


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
    if (getattr(model, "owner_user_id", None) != owner or provider is None
            or getattr(provider, "owner_user_id", None) != owner):
        raise PermissionError("Model is unavailable in this personal catalog")
