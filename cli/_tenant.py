"""Resolve a workspace for a CLI command.

The CLI runs with the tenant guard switched off (developer mode — see
`cli/main.py::_run_platform`), so a command that must act *inside* one
workspace resolves it explicitly by slug or id and runs inside that scope.
`credentials` and `scenarios` already took a `--tenant` option; this module is
the single implementation they all share.

An empty value means "no explicit workspace" — callers either fall back to the
bootstrap tenant (creates) or stay global (reads).
"""

from __future__ import annotations


async def resolve_tenant_id(value: str | None) -> int | None:
    """Workspace id for a slug or numeric id; None when `value` is empty."""
    from app.models.managers.tenant_manager import tenants

    if value is None or not str(value).strip():
        return None

    raw = str(value).strip()
    tenant = await tenants.get(id=int(raw)) if raw.isdigit() else await tenants.get_by_slug(raw)
    if tenant is None:
        raise ValueError(f"Workspace {raw!r} not found")
    return tenant.id


async def tenant_label(tenant_id: int) -> str:
    """`slug (name)` for CLI output, or `?` when the row is gone."""
    from app.models.managers.tenant_manager import tenants

    tenant = await tenants.get(id=tenant_id)
    return f"{tenant.slug} ({tenant.name})" if tenant else "?"
