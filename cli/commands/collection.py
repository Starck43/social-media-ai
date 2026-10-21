"""Task-scoped collection lanes for an existing task.

`collection lanes` moves a task that collects from workspace-level sources
onto its own search lanes: a `Source` row per phrase that only this task may
read. `collection import` feeds a pasted list of VK communities and people
into a task as wall lanes. Without `--apply` the commands print the plan and
write nothing.
"""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(name="collection", help="Manage task-scoped collection lanes")

# The import command is vk-only: the paste format (vk.com URLs, club/public
# prefixes) is VK's, and the two identity resolvers are VK's.
PLATFORM = "vk"


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


@app.command("import")
def import_lanes(
    task_id: int = typer.Argument(..., help="AgentTask id to attach the lanes to"),
    community: list[str] = typer.Option(
        [], "--community", help="Community: URL, club/public/event id or screen name (repeatable)"
    ),
    person: list[str] = typer.Option([], "--person", help="Person: URL, id12345 or screen name (repeatable)"),
    file: Path = typer.Option(
        None, "--file", dir_okay=False, help="Entries one per line (# comments and blank lines ignored)"
    ),
    tenant: str = typer.Option(None, "--tenant", help="Workspace slug or id of the task"),
    apply: bool = typer.Option(False, "--apply", help="Write the plan (default: dry run)"),
):
    """Import VK communities and people from a pasted list as wall lanes.

    Identity resolution only: VK is read (groups.getById, users.get), content
    is never collected and nothing is written to VK.
    """
    from rich import print as rprint
    from rich.markup import escape
    from rich.table import Table

    from app.core.tenant_context import tenant_scope
    from app.models import AgentTask, Platform, Source
    from app.services.collection.lane_import import (
        KIND_COMMUNITY,
        KIND_PERSON,
        LaneMigrationError,
        apply_lane_import,
        parse_lane_entry,
        plan_lane_import,
        resolve_lane_targets,
    )
    from app.services.social.credentials import AuthorizationRequired
    from cli._tenant import resolve_tenant_id, tenant_label

    async def _run_task():
        # vk.com short prefixes bind the kind per entry; --community/--person
        # force it. The file resolves whatever each line means on its own.
        entries = [parse_lane_entry(raw, kind=KIND_COMMUNITY) for raw in community]
        entries += [parse_lane_entry(raw, kind=KIND_PERSON) for raw in person]
        if file is not None:
            try:
                text = Path(file).read_text(encoding="utf-8")
            except OSError as error:
                rprint(f"[red]Cannot read {file}: {error}[/red]")
                raise typer.Exit(1) from error
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                entries.append(parse_lane_entry(stripped))
        if not entries:
            rprint("[red]No entries given: pass --community/--person or --file[/red]")
            raise typer.Exit(1)

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

            targets = await resolve_lane_targets(entries, platform=PLATFORM)
            workspace_sources = []
            platform = await Platform.objects.filter(platform_type=PLATFORM).first()
            if platform is not None:
                workspace_sources = await Source.objects.filter(
                    tenant_id=task.tenant_id,
                    platform_id=platform.id,
                ).select_related("platform")
            plan = plan_lane_import(task, targets, platform=PLATFORM, workspace_sources=workspace_sources)

            sources = {
                int(source.id): (str(source.name or ""), str(source.external_id or ""))
                for source in (task.sources or [])
            }
            rprint(
                f"[bold]Task {plan.task_id}[/bold] '{escape(plan.task_name)}' "
                f"· workspace {plan.tenant_id} · platform {escape(plan.platform)}"
            )
            table = Table(title="Entries")
            for column in ("requested", "resolved", "name", "action"):
                table.add_column(column)
            for decision in plan.decisions:
                if decision.action == "skip":
                    action = f"skip: {decision.reason}"
                elif decision.action == "reuse":
                    action = f"reuse #{decision.source_id}"
                else:
                    action = "create"
                resolved = f"{decision.kind} {decision.external_id}" if decision.external_id else "—"
                table.add_row(escape(decision.raw), escape(resolved), escape(decision.name), escape(action))
            rprint(table)

            rprint("[bold]Lanes the task will be linked to[/bold]")
            if plan.linked_source_ids:
                for source_id in plan.linked_source_ids:
                    rprint(f"  {source_id} {_label(sources.get(source_id))}")
            else:
                rprint("  [dim]none[/dim]")
            if any(decision.action == "create" for decision in plan.decisions):
                rprint("  [dim](created lanes join the list at apply time)[/dim]")

            if not apply:
                where = await tenant_label(task.tenant_id)
                rprint(f"[dim]Dry run — nothing was written. Workspace: {where}[/dim]")
                return

            result = await apply_lane_import(plan)
            rprint("[bold green]Applied[/bold green]")
            rprint(f"  created lanes: {result.created_source_ids or '—'}")
            rprint(f"  reused sources: {result.reused_source_ids or '—'}")
            rprint(f"  linked sources: {result.linked_source_ids or '—'}")
            for line in result.refused:
                rprint(f"[yellow]{escape(line)}[/yellow]")

    try:
        _run(_run_task())
    except LaneMigrationError as error:
        rprint(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    except AuthorizationRequired as error:
        # The import cannot even resolve identities without a VK token.
        rprint(f"[red]{error}[/red]")
        if error.hint:
            rprint(f"[dim]{error.hint}[/dim]")
        raise typer.Exit(1) from error
    except ValueError as error:
        # `resolve_tenant_id` rejects an unknown workspace the same way.
        rprint(f"[red]{error}[/red]")
        raise typer.Exit(1) from error

