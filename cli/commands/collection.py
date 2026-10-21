"""Task-scoped collection lanes for an existing task.

`collection lanes` moves a task that collects from workspace-level sources
onto its own search lanes: a `Source` row per phrase that only this task may
read. Without `--apply` the command prints the plan and writes nothing.
"""

from __future__ import annotations

import typer

app = typer.Typer(name="collection", help="Manage task-scoped collection lanes")


def _run(coro):
    """Run an async command as the platform owner (bypass tenant scope)."""
    import asyncio

    from app.core.permissions import operator_permission_scope
    from app.core.tenant_context import tenant_scope

    with tenant_scope(bypass=True), operator_permission_scope():
        return asyncio.run(coro)


def _split_list(raw: str) -> list[str]:
    """Comma separated values, whitespace trimmed."""
    return [" ".join(value.split()) for value in (raw or "").split(",") if value.strip()]


def _print_plan(plan, sources: dict[int, tuple[str, str]]) -> None:
    """The plan as the operator reads it: lanes, block, retirements, links."""
    from rich import print as rprint
    from rich import print_json
    from rich.markup import escape
    from rich.table import Table

    rprint(
        f"[bold]Task {plan.task_id}[/bold] '{escape(plan.task_name)}' "
        f"· workspace {plan.tenant_id} · platform {escape(plan.platform)}"
    )

    table = Table(title="Lanes")
    for column in ("external_id", "name", "query"):
        table.add_column(column)
    for spec in plan.lane_specs:
        table.add_row(escape(spec.external_id), escape(spec.name), escape(spec.query))
    rprint(table)

    rprint("[bold]Collection block[/bold]")
    print_json(data=plan.collection_block, ensure_ascii=False)

    rprint("[bold]Retired sources[/bold]")
    if plan.retire_source_ids:
        for source_id in plan.retire_source_ids:
            rprint(f"  {source_id} {_label(sources.get(source_id))}")
    else:
        rprint("  [dim]none[/dim]")

    rprint("[bold]Sources that stay linked[/bold]")
    if plan.keep_source_ids:
        for source_id in plan.keep_source_ids:
            rprint(f"  {source_id} {_label(sources.get(source_id))}")
    else:
        rprint("  [dim]none[/dim]")

    for warning in plan.warnings:
        rprint(f"[yellow]{escape(warning)}[/yellow]")


def _label(entry: tuple[str, str] | None) -> str:
    """`name (external_id)` for a linked source, empty when it is unknown."""
    if not entry:
        return ""
    name, external_id = entry
    return f"— {name} ({external_id})"


@app.command("lanes")
def lanes(
    task_id: int = typer.Argument(..., help="AgentTask id to migrate"),
    query: list[str] = typer.Option([], "--query", help="Search phrase of a lane (repeatable)"),
    content_types: str = typer.Option("posts", "--content-types", help="Comma separated content types"),
    limit: int = typer.Option(1000, "--limit", help="Collection limit"),
    city: str = typer.Option(None, "--city", help="City preference of the collection"),
    city_mode: str = typer.Option("prefer", "--city-mode", help="City mode: prefer | require | review"),
    date_from: str = typer.Option(None, "--date-from", help="Collect from this date (YYYY-MM-DD)"),
    date_to: str = typer.Option(None, "--date-to", help="Collect until this date (YYYY-MM-DD)"),
    retire_source: list[int] = typer.Option(
        [], "--retire-source", help="Legacy search source id to retire (repeatable)"
    ),
    platform: str = typer.Option(
        None, "--platform", help="Platform of the lanes (default: inferred from the task's sources)"
    ),
    tenant: str = typer.Option(None, "--tenant", help="Workspace slug or id of the task"),
    apply: bool = typer.Option(False, "--apply", help="Write the plan (default: dry run)"),
):
    """Plan (and optionally apply) task-scoped collection lanes for a task."""
    from rich import print as rprint

    from app.core.tenant_context import tenant_scope
    from app.models import AgentTask
    from app.services.collection.lane_migration import (
        LaneMigrationError,
        apply_lane_migration,
        plan_lane_migration,
    )
    from cli._tenant import resolve_tenant_id, tenant_label

    async def _run_task():
        tenant_id = await resolve_tenant_id(tenant)
        # The task decides the workspace: it is looked up globally first and
        # everything after that runs inside the task's own tenant scope.
        with tenant_scope(bypass=True):
            found = await AgentTask.objects.filter(id=task_id).first()
        if found is None:
            rprint(f"[red]Task {task_id} not found[/red]")
            raise typer.Exit(1)
        if tenant_id is not None and tenant_id != found.tenant_id:
            rprint(f"[red]Task {task_id} lives in another workspace[/red]")
            raise typer.Exit(1)

        with tenant_scope(found.tenant_id):
            task = await AgentTask.objects.filter(id=task_id).prefetch_related("sources", "sources.platform").first()
            if task is None:
                rprint(f"[red]Task {task_id} not found[/red]")
                raise typer.Exit(1)

            plan = plan_lane_migration(
                task,
                queries=list(query),
                content_types=_split_list(content_types),
                limit=limit,
                city=city,
                city_mode=city_mode,
                date_from=date_from,
                date_to=date_to,
                retire_source_ids=list(retire_source),
                platform=platform,
            )
            sources = {
                int(source.id): (str(source.name or ""), str(source.external_id or ""))
                for source in (task.sources or [])
            }
            _print_plan(plan, sources)

            if not apply:
                where = await tenant_label(task.tenant_id)
                rprint(f"[dim]Dry run — nothing was written. Workspace: {where}[/dim]")
                return

            result = await apply_lane_migration(plan, source_ids_by_external_id=await _lane_sources(plan))
            rprint("[bold green]Applied[/bold green]")
            rprint(f"  created lanes: {result.created_source_ids or '—'}")
            rprint(f"  reused lanes: {result.existing_source_ids or '—'}")
            rprint(f"  linked sources: {result.linked_source_ids or '—'}")
            rprint(f"  retired sources: {result.retired_source_ids or '—'}")

    try:
        _run(_run_task())
    except LaneMigrationError as error:
        rprint(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    except ValueError as error:
        # `resolve_tenant_id` rejects an unknown workspace the same way.
        rprint(f"[red]{error}[/red]")
        raise typer.Exit(1) from error


async def _lane_sources(plan) -> dict[str, int]:
    """The lane rows that already exist: `external_id -> source id`."""
    from app.models import Platform, Source

    platform = await Platform.objects.filter(platform_type=plan.platform).first()
    if platform is None:
        return {}
    rows = await Source.objects.filter(
        tenant_id=plan.tenant_id,
        platform_id=platform.id,
        external_id__in=[spec.external_id for spec in plan.lane_specs],
    )
    return {row.external_id: int(row.id) for row in rows}
