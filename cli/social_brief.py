"""Operator CLI read of a morning brief as an explicit workspace actor."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from urllib.parse import urlencode

import typer
from rich import print as rprint

app = typer.Typer(name="social-brief", help="Read a social morning brief as a workspace member")


def _parse_date(value: str | None, option: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise typer.BadParameter(f"--{option} must be an ISO date (YYYY-MM-DD)") from None


async def _run_as(tenant_value: str, actor_value: int, coro_fn, **kwargs):
    """Resolve explicit membership under operator scope, then drop that scope."""
    from app.core.permissions import operator_permission_scope, permission_scope
    from app.core.tenant_context import tenant_scope
    from app.models import User
    from app.models.managers.tenant_manager import TenantUserManager
    from cli._tenant import resolve_tenant_id

    with tenant_scope(bypass=True), operator_permission_scope():
        tenant_id = await resolve_tenant_id(tenant_value)
        memberships = await TenantUserManager().web_memberships(actor_value)
        membership = next((row for row in memberships if row.tenant_id == tenant_id), None)
        grants = await TenantUserManager().member_grants(membership)
        actor = await User.objects.prefetch_related("role.permissions").get(id=actor_value) if membership else None
        if tenant_id is None or membership is None or actor is None or not actor.is_active:
            raise PermissionError("active_workspace_actor_required")

    with tenant_scope(tenant_id), permission_scope(actor, member_grants=grants):
        return await coro_fn(tenant_id=tenant_id, user_id=actor.id, **kwargs)


async def preview_run(
    *,
    tenant: str,
    actor: int,
    days: int = 1,
    timezone_name: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    if not 1 <= days <= 31:
        raise ValueError("invalid_report_window")
    if (start is None) != (end is None):
        raise ValueError("invalid_report_window")

    async def _preview(*, tenant_id, user_id):
        from app.services.social.morning_brief import preview

        brief = await preview(
            tenant_id,
            user_id,
            days=days,
            **({"timezone_name": timezone_name} if timezone_name else {}),
            **({"report_start": start, "report_end": end} if start and end else {}),
        )
        result = brief.to_dict()
        query = {"days": days}
        if timezone_name:
            query["tz"] = timezone_name
        if start and end:
            query.update(start=start.isoformat(), end=end.isoformat())
        result["web_href"] = f"/app/digests/morning?{urlencode(query)}"
        result["writes"] = False
        return result

    return await _run_as(tenant, actor, _preview)


@app.command("preview")
def preview_command(
    tenant: str = typer.Option(..., "--tenant", help="Workspace slug or id"),
    actor: int = typer.Option(..., "--actor", min=1, help="Active member user id whose rights are checked"),
    days: int = typer.Option(1, "--days", min=1, max=31),
    timezone_name: str | None = typer.Option(None, "--timezone", help="Optional IANA timezone"),
    start: str | None = typer.Option(None, "--start", help="Frozen report start date, YYYY-MM-DD"),
    end: str | None = typer.Option(None, "--end", help="Frozen report end date, YYYY-MM-DD"),
):
    """Print the read-only, deterministic brief; never sends or stores it."""
    try:
        result = asyncio.run(
            preview_run(
                tenant=tenant,
                actor=actor,
                days=days,
                timezone_name=timezone_name,
                start=_parse_date(start, "start"),
                end=_parse_date(end, "end"),
            )
        )
    except PermissionError:
        rprint("[red]Permission denied: pass an active member with source and analysis view rights.[/red]")
        raise typer.Exit(1) from None
    except ValueError:
        rprint("[red]Invalid report window or timezone.[/red]")
        raise typer.Exit(1) from None
    except Exception:
        rprint("[red]Social brief unavailable.[/red]")
        raise typer.Exit(1) from None
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
