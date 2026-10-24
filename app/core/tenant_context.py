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
_tenant_scope_ids: ContextVar[tuple[int, ...]] = ContextVar("tenant_scope_ids", default=())
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


def current_tenant_ids() -> tuple[int, ...]:
    """Every workspace this task may read, active one first.

    A single-workspace request has exactly one id — the same value as
    `current_tenant_id()`. The console operator may hold several web memberships
    and reads them together (`tenant_scope(..., also=...)`), so managers filter
    on the whole set instead of guessing one workspace.
    """
    scoped = _tenant_scope_ids.get()
    if scoped:
        return scoped
    current = _tenant_id.get()
    return () if current is None else (current,)


def is_bypass() -> bool:
    """True when running as the platform superuser (owner CLI/admin)."""
    return _tenant_bypass.get()


def set_tenant(tenant_id: Optional[int], *, bypass: bool = False):
    """Set the tenant for the current task; returns a token to reset it."""
    token_tenant = _tenant_id.set(tenant_id)
    token_scope = _tenant_scope_ids.set(() if tenant_id is None else (tenant_id,))
    token_bypass = _tenant_bypass.set(bypass)
    return token_tenant, token_scope, token_bypass


def reset_tenant(token) -> None:
    """Restore the previous tenant context (pair with `set_tenant`)."""
    token_tenant, token_scope, token_bypass = token
    _tenant_id.reset(token_tenant)
    _tenant_scope_ids.reset(token_scope)
    _tenant_bypass.reset(token_bypass)


@contextmanager
def tenant_scope(tenant_id: Optional[int] = None, *, bypass: bool = False, also: Optional[tuple[int, ...]] = None):
    """Run a block as `tenant_id` (or as the superuser when `bypass=True`).

    `also` extends the *readable* set past the active workspace without changing
    which one is active: writes still stamp `current_tenant_id()`, while reads
    accept every member workspace. Only the console operator's multi-workspace
    scope uses it.

    Contextvars propagate into the task that `asyncio.run`/`create_task`
    starts, so wrapping a coroutine body works for both sync and async callers.
    """
    extra = tuple(int(value) for value in (also or ()) if tenant_id is None or value != tenant_id)
    token = set_tenant(tenant_id, bypass=bypass)
    if tenant_id is not None and extra:
        scope_token = _tenant_scope_ids.set((int(tenant_id),) + extra)
    else:
        scope_token = None
    try:
        yield
    finally:
        if scope_token is not None:
            _tenant_scope_ids.reset(scope_token)
        reset_tenant(token)


class PlatformScopeMiddleware:
    """Run the operator console as the platform owner; leave clients to their own scope.

    `/admin/*` no longer lands here: `AdminScopeMiddleware` runs first (it is
    registered outermost) and binds the console to its actor — the owner gets
    this same bypass, everyone else their own rows. What remains here is
    infrastructure — `/static/*`, `/health`, `/docs` — plus a fall-through for any
    other operator surface that carries no actor of its own. The CLI is the same
    thing outside HTTP (`tenant_scope(bypass=True)` in
    `cli/main.py::_run_platform`).

    Client surfaces are excluded because they resolve their own tenant and run
    scoped: `/app/*` from web memberships (`TenantUIMiddleware`) and `/api/*`
    from the bearer token + membership (`ApiScopeMiddleware`).
    """

    # Prefixes that own their own (fail-closed) tenant/permission context.
    TENANT_SCOPED_PREFIXES = ("/app", "/api", "/static", "/admin")
    # Exact paths that are infrastructure, not data.
    BYPASS_EXACT_PATHS = ("/health", "/docs", "/redoc", "/openapi.json", "/favicon.ico")

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in self.BYPASS_EXACT_PATHS or any(
            path == prefix or path.startswith(f"{prefix}/") for prefix in self.TENANT_SCOPED_PREFIXES
        ):
            await self.app(scope, receive, send)
            return
        with tenant_scope(bypass=True):
            await self.app(scope, receive, send)
