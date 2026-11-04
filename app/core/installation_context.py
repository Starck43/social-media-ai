"""Explicit actor scope for installation-level trusted-host administration.

The scope is not authorization: the manager independently checks the active role.
"""

from contextlib import contextmanager
from contextvars import ContextVar

_actor: ContextVar[int | None] = ContextVar("installation_actor", default=None)


def installation_actor() -> int | None:
    return _actor.get()


@contextmanager
def installation_scope(actor_id: int | None):
    token = _actor.set(actor_id)
    try:
        yield
    finally:
        _actor.reset(token)
