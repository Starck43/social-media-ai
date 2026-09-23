"""Tenant context: which tenant the current code runs for.

The agent chat, tools and jobs run without an HTTP request, so tenancy is
carried in a contextvar set by the caller:

- `app/agent/runtime.handle_inbound` sets the tenant from the session row.
- `app/jobs/dispatcher.execute_job` sets it from `job.tenant_id`.
- `app/channels/listener` sets it around `_handle_safely` (same value the
  agent resolves — defense in depth, not a second source of truth).

`BaseManager`/`QuerySet` enforce the tenant for every tenant-scoped model
(see `TenantScopedMixin` in `app/models/base.py`): reads are filtered,
creates stamp the tenant, updates/deletes re-check the row's tenant.

Superuser bypass (the bot owner's own CLI/admin/debug work) is an explicit
flag — never "tenant_id=None means everything".
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional

from starlette.types import ASGIApp, Receive, Scope, Send

_tenant_id: ContextVar[Optional[int]] = ContextVar("tenant_id", default=None)
_tenant_bypass: ContextVar[bool] = ContextVar("tenant_bypass", default=False)


class TenantContextError(RuntimeError):
    """Tenant-scoped data was touched without a tenant and without bypass.

    Raised by `BaseManager` (see `app/models/managers/base_manager.py`): reads
    of a `TenantScopedMixin` model need either a tenant in context or an
    explicit bypass. Silence would mean "leak every tenant's rows".
    """


def current_tenant_id() -> Optional[int]:
    """Tenant id for this task, or None outside tenant code."""
    return _tenant_id.get()


def is_bypass() -> bool:
    """True when running as the platform superuser (owner CLI/admin)."""
    return _tenant_bypass.get()


def set_tenant(tenant_id: Optional[int], *, bypass: bool = False):
    """Set the tenant for the current task; returns a token to reset it."""
    token_tenant = _tenant_id.set(tenant_id)
    token_bypass = _tenant_bypass.set(bypass)
    return token_tenant, token_bypass


def reset_tenant(token) -> None:
    """Restore the previous tenant context (pair with `set_tenant`)."""
    token_tenant, token_bypass = token
    _tenant_id.reset(token_tenant)
    _tenant_bypass.reset(token_bypass)


@contextmanager
def tenant_scope(tenant_id: Optional[int] = None, *, bypass: bool = False):
    """Run a block as `tenant_id` (or as the superuser when `bypass=True`).

    Contextvars propagate into the task that `asyncio.run`/`create_task`
    starts, so wrapping a coroutine body works for both sync and async callers.
    """
    token = set_tenant(tenant_id, bypass=bypass)
    try:
        yield
    finally:
        reset_tenant(token)


class PlatformScopeMiddleware:
    """Run every HTTP request as the platform owner (bypass).

    The HTTP surfaces (`/api/v1`, sqladmin) are the operator's console, not a
    client-facing API: clients talk to the agent through messengers. So the
    request runs in the bootstrap workspace with the guard switched off, which
    is exactly what the legacy endpoints assume. A future client-facing API must
    set `tenant_scope()` from the caller's workspace instead of relying on this.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        with tenant_scope(bypass=True):
            await self.app(scope, receive, send)
