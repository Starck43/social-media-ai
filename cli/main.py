import secrets
from contextlib import nullcontext
from typing import Callable

import typer

# Configure logging (console + file -> logs/app.log) for CLI runs.
import app.core.logger  # noqa: F401  (configures the "app" logger handlers)

from .commands import chains, collect, collection, credentials, direct, llm, roles, scenarios

app = typer.Typer(
    name="SMM Admin CLI",
    help="Social Media Manager CLI",
    no_args_is_help=True,
)

app.add_typer(roles.app, name="roles", help="Manage roles and permissions")
app.add_typer(credentials.app, name="credentials", help="Manage platform credentials (tenant vault)")
app.add_typer(llm.app, name="llm", help="Manage LLM providers and models")
app.add_typer(chains.app, name="chains", help="Manage topic chains")
app.add_typer(scenarios.app, name="scenarios", help="Manage agent scenarios")
app.add_typer(collection.app, name="collection", help="Manage task-scoped collection lanes")
app.command(name="collect")(collect.collect_cmd)

# Direct job-type commands at top level: `cli.main analyze|prune|learn|reflect`.
app.command(name="analyze")(direct.cmd_analyze)
app.command(name="prune")(direct.cmd_prune)
app.command(name="learn")(direct.cmd_learn)
app.command(name="reflect")(direct.cmd_reflect)


def _run_platform(coro):
    """Run an async command with the tenant guard switched off (developer mode).

    The CLI is a developer tool, not a user surface: it reads and mutates every
    workspace, so the guard is bypassed and a create without an explicit tenant
    lands in the bootstrap workspace. End users never reach this path — API and
    web requests are pinned to their own workspace by `ApiScopeMiddleware` and
    `TenantUIMiddleware`. Commands that must write into a specific workspace take
    `--tenant` and switch the scope on for that block.
    """
    import asyncio

    from app.core.permissions import operator_permission_scope
    from app.core.tenant_context import tenant_scope

    with tenant_scope(bypass=True), operator_permission_scope():
        return asyncio.run(coro)


def _split_phrases(raw: str | None) -> list[str]:
    """Comma separated phrases: spaces inside a phrase are kept."""
    return [" ".join(v.split()) for v in (raw or "").split(",") if v.strip()]


def _split_names(raw: str | None) -> list[str]:
    """Split a comma/space separated username string into a clean list."""
    if not raw:
        return []
    return [n.strip().lstrip("@") for n in raw.replace(",", " ").split() if n.strip()]


def _split_ints(raw: str | None) -> list[int]:
    """Split a comma/space separated string into a list of ints."""
    if not raw:
        return []
    return [int(p) for p in raw.replace(",", " ").split() if p.strip().isdigit()]


async def _check_task_sources(source_ids: list[int], tenant_id: int) -> None:
    """Reject source ids that are unknown or belong to another workspace.

    `agent_task_sources` has no tenant column, so a raw id would let a task read
    another workspace's content.
    """
    if not source_ids:
        return
    from app.models import Source

    sources = {s.id: s for s in await Source.objects.filter(id__in=source_ids)}
    missing = sorted(set(source_ids) - set(sources))
    if missing:
        raise typer.Exit(f"[red]Unknown source id(s): {', '.join(map(str, missing))}[/red]")
    foreign = sorted(sid for sid, s in sources.items() if s.tenant_id != tenant_id)
    if foreign:
        raise typer.Exit(f"[red]Source(s) {', '.join(map(str, foreign))} belong to another workspace[/red]")


async def _set_task_sources(task_id: int, source_ids: list[int], tenant_id: int) -> None:
    """Link a task to the given sources via the m2m table.

    `agent_task_sources` has no tenant column of its own, so nothing stops a task
    from being linked to a source of another workspace — and the job would then
    read that source's rows. Check the sources against the task's workspace here.
    """
    from app.models.managers.agent_task_manager import AgentTaskManager

    await _check_task_sources(source_ids, tenant_id)
    await AgentTaskManager().add_sources(task_id, source_ids)


task_app = typer.Typer(help="Manage agent tasks")


@task_app.command("plan")
def task_plan_cmd(
    plan_json: str = typer.Argument(..., help="Canonical social task plan as JSON"),
    job_type: str = typer.Option("analyze", "--job-type", help="collect or analyze"),
    tenant: str = typer.Option(None, "--tenant", help="Workspace slug or ID for a live entitlement preview"),
):
    """Preview a plan; --tenant adds a read-only workspace entitlement decision."""
    import json as _json

    from rich import print as rprint

    from app.services.task_plans import apply_task_plan

    try:
        if len(plan_json) > 65_536:
            raise ValueError("Task plan JSON exceeds 64 KiB")
        raw = _json.loads(plan_json)
        if not isinstance(raw, dict):
            raise ValueError("Task plan must be a JSON object")
        payload = apply_task_plan({}, raw, job_type=job_type)
    except (TypeError, ValueError) as error:
        rprint(f"[red]Invalid task plan: {error}[/red]")
        raise typer.Exit(1) from None
    entitlement_preview = {"allowed": None, "reason": "workspace_required", "checked": False}
    if tenant:
        from app.core.tenant_context import tenant_scope
        from app.services.collection.entitlement_policy import preview_entitlements

        async def _preview():
            from ._tenant import resolve_tenant_id

            tenant_id = await resolve_tenant_id(tenant)
            with tenant_scope(tenant_id):
                return await preview_entitlements(payload)

        entitlement_preview = _run_platform(_preview())
    rprint(_json.dumps(
        {"task_plan": payload["task_plan"], "entitlements": entitlement_preview}, ensure_ascii=False, indent=2
    ))
    rprint("[dim]Preview only. Access, payer eligibility and platform coverage are checked at execution.[/dim]")


@task_app.command("entitlements")
def task_entitlements_cmd(
    tenant: str = typer.Option(..., "--tenant", help="Workspace slug or ID"),
):
    """Inspect current product grants and availability; consume no usage."""
    import json as _json

    from rich import print as rprint

    from app.core.tenant_context import tenant_scope
    from app.models.managers.entitlement_manager import EntitlementManager

    async def _inspect():
        from ._tenant import resolve_tenant_id

        tenant_id = await resolve_tenant_id(tenant)
        with tenant_scope(tenant_id):
            return await EntitlementManager().inspect()

    rprint(_json.dumps(_run_platform(_inspect()), ensure_ascii=False, indent=2))


@task_app.command("list")
def task_list():
    """List all tasks."""

    from rich import print as rprint
    from rich.table import Table

    from app.models import AgentTask

    async def _run():
        from app.models.managers.tenant_manager import tenants

        slugs = {t.id: t.slug for t in await tenants.all()}
        rows: list[AgentTask] = await AgentTask.objects.order_by(AgentTask.id)  # type: ignore[misc]
        table = Table(title="AgentTasks (all workspaces)")
        for col in ("id", "workspace", "name", "cron", "job", "active", "next_run_at", "last_status"):
            table.add_column(col)
        for s in rows:
            table.add_row(
                str(s.id),
                slugs.get(s.tenant_id, "?"),
                s.name,
                s.cron_expr,
                s.job_type,
                "yes" if s.is_active else "no",
                s.next_run_at.strftime("%Y-%m-%d %H:%M") if s.next_run_at else "—",
                s.last_status or "—",
            )
        rprint(table)

    _run_platform(_run())


@task_app.command("result")
def task_result_cmd(
    task_id: int = typer.Argument(..., help="AgentTask ID"),
    job_id: int = typer.Option(None, "--job-id", help="Read one retained run instead of the latest run"),
    limit: int = typer.Option(5, min=1, max=10, help="Maximum permitted analysis summaries"),
    tenant: str = typer.Option(..., "--tenant", help="Workspace slug or ID"),
):
    """Read bounded task status and permitted analysis summaries."""
    from rich import print as rprint
    from rich.table import Table

    from app.core.tenant_context import tenant_scope
    from app.services.task_results import task_result

    async def _run():
        from ._tenant import resolve_tenant_id

        tenant_id = await resolve_tenant_id(tenant)
        if tenant_id is None:
            rprint("[red]Specify --tenant to keep task results workspace-scoped.[/red]")
            raise typer.Exit(1)
        with tenant_scope(tenant_id):
            result = await task_result(task_id, job_id=job_id, limit=limit)
        if result is None:
            rprint("[yellow]Task or run not found[/yellow]")
            raise typer.Exit(1)
        rprint(
            f"Task {result['task_id']} · status={result['status'] or 'unknown'} · "
            f"job={result['job_id'] or '—'} ({result['job_status']}) · coverage={result['coverage']}"
        )
        if result["counts"]:
            rprint("Counts: " + ", ".join(f"{key}={value}" for key, value in result["counts"].items()))
        if result["analyses"]:
            table = Table(title="Recent analyses")
            table.add_column("ID")
            table.add_column("Title")
            table.add_column("Summary")
            table.add_column("URL")
            for analysis in result["analyses"]:
                table.add_row(
                    str(analysis["id"]),
                    analysis["title"],
                    analysis["summary"] or "—",
                    analysis["url"],
                )
            rprint(table)
        else:
            rprint("No analysis summaries are available to this operator.")

    _run_platform(_run())


async def _add_task(
    name: str,
    cron: str,
    job_type: str,
    parsed_payload: dict,
    parsed_source_ids: list[int],
    scenario_id: int | None,
    tenant: str | None,
):
    """Create a task in `tenant` (or the bootstrap workspace) and report where.

    Returns the created task.
    """
    from datetime import datetime, timedelta, timezone

    from rich import print as rprint

    from app.core.tenant_context import tenant_scope
    from app.models import AgentTask
    from app.models.managers.agent_task_manager import AgentTaskManager
    from app.models.managers.tenant_manager import tenants
    from app.services.collection.intent import (
        CollectionIntentError,
        apply_collection,
        collection_error_message,
    )
    from app.tasks.cron import next_run_at

    from ._tenant import resolve_tenant_id, tenant_label

    if not isinstance(parsed_payload, dict):
        rprint("[red]Payload must be a JSON object[/red]")
        raise typer.Exit(1)
    try:
        parsed_payload = apply_collection(
            parsed_payload,
            parsed_payload.get("collection"),
            job_type=job_type,
        )
    except CollectionIntentError as error:
        rprint(f"[red]{collection_error_message(str(error))}[/red]")
        raise typer.Exit(1) from error

    tenant_id = await resolve_tenant_id(tenant)
    with tenant_scope(tenant_id) if tenant_id else nullcontext():
        if await AgentTask.objects.get(name=name):
            rprint(f"[red]AgentTask '{name}' already exists[/red]")
            raise typer.Exit(1)
        # Checked before the insert: a rejected source must not leave a task
        # behind that silently runs "all active sources".
        await _check_task_sources(parsed_source_ids, tenant_id)

        # The workspace tier caps how many scheduled tasks it may hold. The CLI
        # is an operator surface, so it is still the tier that answers — not an
        # "administrators may exceed the quota" escape hatch.
        tenant_row = await tenants.get(id=tenant_id) if tenant_id else None
        if tenant_id:
            from app.services.tenancy.limits import check_task_limit

            if tenant_row is not None:
                blocked = await check_task_limit(tenant_row)
                if blocked:
                    rprint(f"[red]{blocked}[/red]")
                    raise typer.Exit(1)
        # `@once` is not a croniter expression, so it gets a near-term fire time
        # (same rule as the web form) instead of going through next_run_at().
        from app.services.task_plans import task_plan_timezone
        from app.tasks.cron import resolve_tz

        plan_tz = task_plan_timezone(parsed_payload.get("task_plan"), resolve_tz(tenant_row))
        if cron.strip() == "@once":
            next_run = datetime.now(timezone.utc) + timedelta(minutes=1)
        else:
            next_run = next_run_at(cron, plan_tz)
        if isinstance(parsed_payload.get("task_plan"), dict):
            from app.core.database import new_session
            from app.core.permissions import service_permission_scope

            session = new_session()
            try:
                async with session.begin():
                    task = await AgentTaskManager().create(
                        session=session,
                        name=name,
                        cron_expr=cron,
                        job_type=job_type,
                        payload=parsed_payload,
                        agent_scenario_id=scenario_id,
                        is_active=False,
                        next_run_at=next_run,
                    )
                    with service_permission_scope("agenttask", "update"):
                        await AgentTaskManager().set_sources(task.id, parsed_source_ids, session=session)
            finally:
                await session.close()
            rprint(
                "[yellow]Universal-plan task was saved inactive. Activate it from the workspace after the owner configures payer consent.[/yellow]"
            )
        else:
            task = await AgentTaskManager().create(
                name=name,
                cron_expr=cron,
                job_type=job_type,
                payload=parsed_payload,
                agent_scenario_id=scenario_id,
                is_active=True,
                next_run_at=next_run,
            )
            if parsed_source_ids:
                await _set_task_sources(task.id, parsed_source_ids, task.tenant_id)

        # Hint when the bound scenario wants specific targets the task does not
        # carry — the analysis would otherwise have nothing to aim at.
        if scenario_id:
            from app.models import AgentScenario
            from app.services.ai.param_registry import missing_target_params

            scenario = await AgentScenario.objects.get(id=scenario_id)
            if scenario is not None:
                for missing in missing_target_params(scenario.analysis_types, parsed_payload):
                    rprint(
                        f"[yellow]Hint: сценарий «{scenario.name}» использует {missing}, но в задаче не указан.[/yellow]"
                    )
    where = await tenant_label(task.tenant_id)
    rprint(f"[green]AgentTask '{name}' created in {where} ({cron}, {job_type})[/green]")
    return task


@task_app.command("add")
def task_add(
    name: str = typer.Argument(..., help="Unique task name"),
    cron: str = typer.Argument(..., help="Cron expression, e.g. '0 9 * * *' (or @once)"),
    job_type: str = typer.Argument(..., help="Job type: collect | digest | prune | analyze | learn | reflect"),
    source_ids: str = typer.Option(
        None, "--sources", "-s", help="Comma/space separated source IDs to link (empty = all active)"
    ),
    tracked_users: str = typer.Option(
        None,
        "--tracked",
        help="Tracked authors, platform:id (e.g. vk:123456): only their items are analysed",
    ),
    scenario_id: int = typer.Option(None, "--scenario", help="AgentScenario ID to apply when the task runs"),
    start_date: str = typer.Option(None, "--start-date", help="Collect content from this date (DD-MM-YYYY) for collect/analyze"),
    end_date: str = typer.Option(None, "--end-date", help="Collect content until this date (DD-MM-YYYY)"),
    force_refresh: bool = typer.Option(
        False, "--force-refresh", help="Re-fetch the whole window on every run (collect); stored on the task"
    ),
    payload: str = typer.Option("{}", "--payload", "-p", help='Extra JSON payload, e.g. \'{"period": "week"}\''),
    task_plan: str = typer.Option(None, "--task-plan", help="Canonical social task plan JSON; source scope is explicit"),
    tenant: str = typer.Option(None, "--tenant", help="Workspace slug or id to create the task in"),
    # Specific analysis targets — they belong on the task (payload), not the
    # scenario (methodology), see app/services/ai/param_registry.py.
    brands: str = typer.Option(None, "--brands", help="Comma/space separated brands to track (brand_mentions)"),
    competitors: str = typer.Option(
        None, "--competitors", help="Comma/space separated competitors to analyze (competitor)"
    ),
    hashtags: str = typer.Option(None, "--hashtags", help="Comma/space separated hashtags to track (hashtag_analysis)"),
    influencers: str = typer.Option(
        None, "--influencers", help="Comma/space separated authors/influencers to analyze (influencer)"
    ),
    keywords: str = typer.Option(None, "--keywords", help="Comma/space separated keywords to search (keywords)"),
    topics: str = typer.Option(None, "--topics", help="Comma/space separated topics to analyze (topics)"),
):
    """Add a task."""
    import json as _json

    from rich import print as rprint

    from app.models.managers.agent_task_manager import AgentTaskManager

    if not AgentTaskManager().validate_cron(cron):
        rprint(f"[red]Invalid cron expression: {cron}[/red]")
        raise typer.Exit(1)
    from app.jobs.handlers import HANDLERS

    if job_type not in HANDLERS:
        rprint(f"[red]job_type must be one of: {', '.join(HANDLERS.keys())}[/red]")
        raise typer.Exit(1)

    try:
        parsed_payload = _json.loads(payload)
    except _json.JSONDecodeError as error:
        rprint(f"[red]Invalid payload JSON: {error}[/red]")
        raise typer.Exit(1) from error
    if not isinstance(parsed_payload, dict):
        rprint("[red]Payload must be a JSON object[/red]")
        raise typer.Exit(1)
    parsed_source_ids = _split_ints(source_ids)
    if task_plan is not None:
        from app.services.task_plans import TaskPlanError, apply_task_plan, parse_task_plan

        try:
            raw_plan = _json.loads(task_plan)
            plan = parse_task_plan(raw_plan)
            planned_cron = "@once" if plan.trigger.type == "once" else plan.trigger.cron
            if cron != planned_cron:
                raise TaskPlanError("cron must match task_plan.trigger")
            if source_ids is not None and parsed_source_ids != list(plan.source_ids):
                raise TaskPlanError("--sources must match task_plan.source_scope and source_ids")
            if scenario_id is not None and scenario_id != plan.scenario_id:
                raise TaskPlanError("--scenario must match task_plan.scenario_id")
            parsed_source_ids = list(plan.source_ids)
            scenario_id = plan.scenario_id
            parsed_payload = apply_task_plan(parsed_payload, raw_plan, job_type=job_type)
        except (ValueError, TypeError) as error:
            rprint(f"[red]Invalid task plan: {error}[/red]")
            raise typer.Exit(1) from error
    from app.services.monitoring.selection import SelectionError

    try:
        parsed_payload = AgentTaskManager.build_selection_payload(
            tracked_users=_split_names(tracked_users) if tracked_users is not None else None,
            keywords=_split_phrases(keywords) if keywords is not None else None,
            existing=parsed_payload,
        )
    except SelectionError as error:
        rprint(f"[red]Invalid selection: {error}[/red]")
        raise typer.Exit(1) from error
    for raw, key in (
        (brands, "brands"),
        (competitors, "competitors"),
        (hashtags, "hashtags"),
        (influencers, "influencer_names"),
        (keywords, "keywords_list"),
        (topics, "topic_list"),
    ):
        if raw:
            parsed_payload[key] = [t.strip() for t in raw.replace(",", " ").split() if t.strip()]

    # Stored on the task the same way the web form does — a collect/analyze task
    # without a `cli_dates.start_date` would drain a fresh source from the first
    # post. `--force-refresh` marks the whole window for overwrite on each run.
    if start_date or end_date or force_refresh:
        from app.models.managers.agent_task_manager import AgentTaskManager

        start = AgentTaskManager.parse_date(start_date) if start_date else None
        end = AgentTaskManager.parse_date(end_date) if end_date else None
        parsed_payload.update(AgentTaskManager.build_dates_payload(start, end, force_refresh=force_refresh))

    _run_platform(
        _add_task(
            name=name,
            cron=cron,
            job_type=job_type,
            parsed_payload=parsed_payload,
            parsed_source_ids=parsed_source_ids,
            scenario_id=scenario_id,
            tenant=tenant,
        )
    )


async def _remove_task(name: str) -> int:
    """Delete every task with this name, naming the workspaces it touched.

    Task names are unique per workspace, not globally, and the CLI is developer
    mode — so a name can legitimately exist in several workspaces. Deleting them
    all is the intended behaviour; staying silent about it is not.
    """
    from rich import print as rprint

    from app.models import AgentTask

    from ._tenant import tenant_label

    matches = await AgentTask.objects.filter(name=name)
    if not matches:
        rprint("[yellow]Not found[/yellow]")
        return 0
    where = ", ".join(sorted({await tenant_label(s.tenant_id) for s in matches}))
    deleted = await AgentTask.objects.delete(name=name)
    rprint(f"[green]Deleted {deleted} task(s)[/green] in {where}")
    return deleted


@task_app.command("remove")
def task_remove(name: str = typer.Argument(...)):
    """Remove a task by name (in every workspace that has one)."""

    _run_platform(_remove_task(name))


@task_app.command("pause")
def task_pause(name: str = typer.Argument(...), resume: bool = typer.Option(False, "--resume")):
    """Pause (or --resume) a task."""

    from rich import print as rprint

    from app.models import AgentTask

    async def _run():
        from ._tenant import tenant_label

        matches: list[AgentTask] = await AgentTask.objects.filter(name=name)
        if not matches:
            rprint("[yellow]Not found[/yellow]")
            raise typer.Exit(1)
        for s in matches:
            await AgentTask.objects.update_by_id(s.id, is_active=resume)
        where = ", ".join(sorted({await tenant_label(s.tenant_id) for s in matches}))
        rprint(f"[green]{'Resumed' if resume else 'Paused'} '{name}'[/green] in {where}")

    _run_platform(_run())


@task_app.command("update")
def task_update(
    name: str = typer.Argument(..., help="Task name to update"),
    job_type: str = typer.Option(None, "--job-type", help="New job type: collect | digest | prune | analyze | learn | reflect"),
    cron: str = typer.Option(None, "--cron", help="New cron expression (5 fields) or @once"),
    source_ids: str = typer.Option(
        None, "--sources", "-s", help="New comma/space separated source IDs (empty = all active)"
    ),
    tracked_users: str = typer.Option(
        None, "--tracked", help="New tracked authors, platform:id"
    ),
    scenario_id: int = typer.Option(None, "--scenario", help="New AgentScenario ID"),
    period: str = typer.Option(None, "--period", help="Period for digest: day | week"),
    start_date: str = typer.Option(None, "--start-date", help="New start_date (YYYY-MM-DD) for collect/analyze"),
    end_date: str = typer.Option(None, "--end-date", help="New end_date (YYYY-MM-DD)"),
    force_refresh: bool = typer.Option(
        None, "--force-refresh", help="New force_refresh value (collect)"
    ),
    force_reanalyze: bool = typer.Option(
        None, "--force-reanalyze", help="New force_reanalyze value (analyze)"
    ),
    payload: str = typer.Option(
        None,
        "--payload",
        "-p",
        help='JSON object merged into the task payload, e.g. \'{"collection": {"content_types": ["posts", "comments"]}}\'',
    ),
    task_plan: str = typer.Option(None, "--task-plan", help="Replace the canonical social task plan JSON"),
    brands: str = typer.Option(None, "--brands", help="New comma/space separated brands"),
    competitors: str = typer.Option(
        None, "--competitors", help="New comma/space separated competitors"
    ),
    hashtags: str = typer.Option(None, "--hashtags", help="New comma/space separated hashtags"),
    influencers: str = typer.Option(
        None, "--influencers", help="New comma/space separated authors/influencers"
    ),
    keywords: str = typer.Option(None, "--keywords", help="New comma/space separated keywords"),
    topics: str = typer.Option(None, "--topics", help="New comma/space separated topics"),
    tenant: str = typer.Option(None, "--tenant", help="Workspace slug or id (for global lookup)"),
):
    """Update an existing task. Only provided fields are changed."""
    import json as _json

    from rich import print as rprint

    from app.core.tenant_context import tenant_scope
    from app.models import AgentTask, Tenant
    from app.models.managers.agent_task_manager import AgentTaskManager
    from app.services.collection.intent import (
        CollectionIntentError,
        apply_collection,
        collection_error_message,
    )
    from app.tasks.cron import next_run_at, resolve_tz

    from ._tenant import resolve_tenant_id, tenant_label

    parsed_patch: dict | None = None
    parsed_plan: dict | None = None
    if task_plan is not None:
        try:
            parsed_plan = _json.loads(task_plan)
        except _json.JSONDecodeError as error:
            rprint(f"[red]Invalid task plan JSON: {error}[/red]")
            raise typer.Exit(1) from error
        if not isinstance(parsed_plan, dict):
            rprint("[red]Task plan must be a JSON object[/red]")
            raise typer.Exit(1)
    if payload is not None:
        try:
            parsed_patch = _json.loads(payload)
        except _json.JSONDecodeError as error:
            rprint(f"[red]Invalid payload JSON: {error}[/red]")
            raise typer.Exit(1) from error
        if not isinstance(parsed_patch, dict):
            rprint("[red]Payload must be a JSON object[/red]")
            raise typer.Exit(1)

    async def _run():
        tenant_id = await resolve_tenant_id(tenant)
        with tenant_scope(tenant_id) if tenant_id else nullcontext():
            matches: list[AgentTask] = await AgentTask.objects.filter(name=name)
            if not matches:
                rprint("[yellow]Not found[/yellow]")
                raise typer.Exit(1)

            from app.jobs.handlers import HANDLERS

            if job_type is not None and job_type not in HANDLERS:
                rprint(f"[red]job_type must be one of: {', '.join(HANDLERS.keys())}[/red]")
                raise typer.Exit(1)
            if cron is not None and not AgentTaskManager.validate_cron(cron):
                rprint(f"[red]Invalid cron expression: {cron}[/red]")
                raise typer.Exit(1)

            for task in matches:
                updated_payload = dict(task.payload) if task.payload else {}
                stored_plan = updated_payload.get("task_plan")
                if tracked_users is not None or keywords is not None:
                    try:
                        updated_payload = AgentTaskManager.build_selection_payload(
                            tracked_users=_split_names(tracked_users) if tracked_users is not None else None,
                            keywords=_split_phrases(keywords) if keywords is not None else None,
                            existing=updated_payload,
                        )
                    except Exception as error:  # selection input, not a runtime fault
                        rprint(f"[red]Invalid selection: {error}[/red]")
                        raise typer.Exit(1)
                if period is not None:
                    updated_payload["period"] = period
                for raw, key in (
                    (brands, "brands"),
                    (competitors, "competitors"),
                    (hashtags, "hashtags"),
                    (influencers, "influencer_names"),
                    (keywords, "keywords_list"),
                    (topics, "topic_list"),
                ):
                    if raw is not None:
                        updated_payload[key] = [t.strip() for t in raw.replace(",", " ").split() if t.strip()]

                # Handle date fields for collect/analyze
                new_job_type = job_type if job_type is not None else task.job_type
                if stored_plan is None and AgentTaskManager.requires_content_dates(new_job_type):
                    start = AgentTaskManager.parse_date(start_date) if start_date else None
                    end = AgentTaskManager.parse_date(end_date) if end_date else None
                    fr = force_refresh if force_refresh is not None else task.payload.get("force_refresh", False)
                    updated_payload.update(AgentTaskManager.build_dates_payload(start, end, force_refresh=fr))
                    if force_reanalyze is not None:
                        updated_payload["force_reanalyze"] = force_reanalyze
                elif stored_plan is None and (start_date or end_date or force_refresh is not None):
                    start = AgentTaskManager.parse_date(start_date) if start_date else None
                    end = AgentTaskManager.parse_date(end_date) if end_date else None
                    fr = force_refresh if force_refresh is not None else task.payload.get("force_refresh", False)
                    updated_payload.update(AgentTaskManager.build_dates_payload(start, end, force_refresh=fr))
                    if force_reanalyze is not None:
                        updated_payload["force_reanalyze"] = force_reanalyze

                if parsed_patch is not None:
                    updated_payload.update(parsed_patch)
                from app.services.task_plans import prepare_task_plan_update

                policy_input = {"payload": updated_payload}
                for key, value in (
                    ("cron_expr", cron),
                    ("agent_scenario_id", scenario_id),
                    ("start_date", start_date),
                    ("end_date", end_date),
                ):
                    if value is not None:
                        policy_input[key] = value
                if source_ids is not None:
                    policy_input["source_ids"] = _split_ints(source_ids)
                if parsed_plan is not None:
                    policy_input["task_plan"] = parsed_plan
                try:
                    mutation = prepare_task_plan_update(task.payload, policy_input, job_type=new_job_type)
                except ValueError as error:
                    rprint(f"[red]Invalid task plan: {error}[/red]")
                    raise typer.Exit(1) from error
                if mutation.cleared:
                    stored_plan = None
                planned_sources = mutation.changes.get("source_ids")
                new_cron = mutation.changes.get("cron_expr", task.cron_expr)
                scenario_id_to_store = mutation.changes.get("agent_scenario_id", task.agent_scenario_id)
                updated_payload = mutation.changes.get("payload", updated_payload)
                try:
                    updated_payload = apply_collection(
                        updated_payload,
                        updated_payload.get("collection"),
                        job_type=new_job_type,
                    )
                except CollectionIntentError as error:
                    rprint(f"[red]{collection_error_message(str(error))}[/red]")
                    raise typer.Exit(1) from error

                updates: dict = {
                    "job_type": new_job_type,
                    "payload": updated_payload,
                }
                if "agent_scenario_id" in mutation.changes:
                    updates["agent_scenario_id"] = scenario_id_to_store
                if cron is not None or parsed_plan is not None:
                    tenant_row = await Tenant.objects.get(id=task.tenant_id)
                    from app.services.task_plans import task_plan_timezone

                    plan_tz = task_plan_timezone(
                        parsed_plan if parsed_plan is not None else stored_plan,
                        resolve_tz(tenant_row),
                    )
                    if new_cron.strip() == "@once":
                        from datetime import datetime, timedelta, timezone

                        updates["next_run_at"] = datetime.now(timezone.utc) + timedelta(minutes=1)
                    else:
                        updates["next_run_at"] = next_run_at(new_cron, plan_tz)

                if parsed_plan is not None:
                    from app.core.database import new_session
                    from app.core.permissions import service_permission_scope

                    await _check_task_sources(planned_sources or [], task.tenant_id)
                    session = new_session()
                    try:
                        async with session.begin():
                            await AgentTask.objects.update_by_id(task.id, session=session, **updates)
                            with service_permission_scope("agenttask", "update"):
                                await AgentTaskManager().set_sources(
                                    task.id, planned_sources or [], session=session
                                )
                    finally:
                        await session.close()
                else:
                    await AgentTask.objects.update_by_id(task.id, **updates)
                    if source_ids is not None:
                        parsed = _split_ints(source_ids)
                        await _check_task_sources(parsed, task.tenant_id)
                        await AgentTaskManager().set_sources(task.id, parsed)

                where = await tenant_label(task.tenant_id)
                changes = [f"{k}={v}" for k, v in {
                    "job_type": new_job_type,
                    "cron": new_cron,
                    "scenario": scenario_id,
                }.items() if {k: {"job_type": job_type, "cron": cron, "scenario": scenario_id}.get(k)}]
                rprint(f"[green]Updated '{name}' in {where}[/green]")

    _run_platform(_run())


async def _run_task(
    task: str | None,
    job_type: str,
    sources: str | None,
    tracked: str | None,
    scenario: int | None,
    period: str | None,
    tenant: str | None,
    force_refresh: bool = False,
    start_date: str | None = None,
    end_date: str | None = None,
    brands: str | None = None,
    competitors: str | None = None,
    hashtags: str | None = None,
    influencers: str | None = None,
    keywords: str | None = None,
    topics: str | None = None,
) -> dict:
    """Run one task now and return the job outcome.

    The task row decides whose workflow this is: its workspace is used for the
    job and for the bookkeeping. Only the job created here is executed — running
    a task never drains an unrelated pending job that happens to be older in the
    queue (which is what `drain()` used to do).

    `--force-refresh` is a full-cycle override: it re-fetches the whole period
    and (for `collect`) re-analyzes it, overwriting rows by (source, date). It
    is merged into the job payload only — the task row is left unchanged. A task
    that already stores `cli_dates`/`force_refresh` in its payload keeps them
    unless the command-line flags override them (the stored values flow through
    the task payload either way).
    """
    from rich import print as rprint

    from app.core.tenant_context import tenant_scope
    from app.jobs.dispatcher import run_job_now
    from app.jobs.enqueue import enqueue_task_run
    from app.models import AgentTask
    from app.models.managers.agent_task_manager import AgentTaskManager

    from ._tenant import resolve_tenant_id, tenant_label

    if task:
        target = await AgentTask.objects.get(id=int(task)) if task.isdigit() else None
        if target is None:
            target = await AgentTask.objects.get(name=task)
        if target is None:
            rprint(f"[red]Task '{task}' not found[/red]")
            raise typer.Exit(1)
    else:
        from datetime import datetime, timezone

        tenant_id = await resolve_tenant_id(tenant)
        payload: dict = {}
        if tracked:
            from app.services.monitoring.selection import SelectionError

            try:
                payload = AgentTaskManager.build_selection_payload(
                    tracked_users=_split_names(tracked), keywords=_split_phrases(keywords), existing=payload
                )
            except SelectionError as error:
                rprint(f"[red]Invalid selection: {error}[/red]")
                raise typer.Exit(1) from error
        if period:
            payload["period"] = period
        if start_date or end_date:
            from app.models.managers.agent_task_manager import AgentTaskManager

            start = AgentTaskManager.parse_date(start_date) if start_date else None
            end = AgentTaskManager.parse_date(end_date) if end_date else None
            payload.update(AgentTaskManager.build_dates_payload(start, end, force_refresh=force_refresh))
        elif force_refresh:
            payload["force_refresh"] = True
        for raw, key in (
            (brands, "brands"),
            (competitors, "competitors"),
            (hashtags, "hashtags"),
            (influencers, "influencer_names"),
            (keywords, "keywords_list"),
            (topics, "topic_list"),
        ):
            if raw:
                payload[key] = [t.strip() for t in raw.replace(",", " ").split() if t.strip()]
        with tenant_scope(tenant_id) if tenant_id else nullcontext():
            # `@once` is created disarmed: a still-armed one-shot would be picked
            # up by the scheduler tick and enqueue a duplicate job. The name
            # carries a timestamp and a random suffix because task names are
            # unique per workspace: two one-offs started within the same second
            # would otherwise collide on that constraint.
            stamp = datetime.now(timezone.utc)
            target = await AgentTask.objects.create(
                name=f"one-off-{job_type}-{stamp:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}",
                cron_expr="@once",
                job_type=job_type,
                payload=payload,
                agent_scenario_id=scenario,
                is_active=False,
                next_run_at=None,  # disarmed one-shot: no scheduled fire
            )
            if _split_ints(sources):
                await _set_task_sources(target.id, _split_ints(sources), target.tenant_id)

    # Full-cycle refresh: for `collect` re-fetch + re-analyze (overwrite by
    # source/date); for other job types just pass the flag so the handler can
    # decide what it refreshes. Applied only to this job, never to the task row.
    refresh_extra: dict = {}
    if force_refresh:
        refresh_extra = {"force_refresh": True}
        if job_type in ("collect", "analyze"):
            refresh_extra["force_reanalyze"] = True

    where = await tenant_label(target.tenant_id)
    job = await enqueue_task_run(target, extra_payload=refresh_extra, supersede_unfinished=True)
    rprint(f"[green]Job {job.id} enqueued for task '{target.name}'[/green] in {where}")
    outcome = await run_job_now(job.id, allow_retry=False) or {}
    if outcome.get("status") == "done":  # "done" is the terminal success status
        rprint(f"[bold green]Finished[/bold green]: {outcome.get('result')}")
    else:
        rprint(f"[red]{outcome.get('status') or 'not claimed'}[/red]: {outcome.get('error') or ''}")
        raise typer.Exit(1)
    return outcome


def _make_task_run_command(job_type: str) -> Callable:
    """Build a `task <job_type>` subcommand that runs an existing (or one-off) task."""

    def run(
        task: str = typer.Option(None, "--task", "-t", help="Existing task by name or id to run"),
        sources: str = typer.Option(None, "--sources", "-s", help="Source IDs to link (one-off run)"),
        tracked: str = typer.Option(
            None, "--tracked", help="Tracked authors, platform:id (one-off run)"
        ),
        keywords: str = typer.Option(None, "--keywords", help="Text scope phrases (one-off run)"),
        scenario: int = typer.Option(None, "--scenario", help="AgentScenario ID (one-off run)"),
        period: str = typer.Option(None, "--period", help="Period for digest/collect: day | week | last month etc."),
        tenant: str = typer.Option(None, "--tenant", help="Workspace slug or id for a one-off run"),
        force_refresh: bool = typer.Option(
            False,
            "--force-refresh",
            help="Full-cycle refresh: for collect re-fetch + re-analyze the whole period (overwrites rows by source/date)",
        ),
        start_date: str = typer.Option(None, "--start-date", help="Collect from this date (DD-MM-YYYY) — one-off only"),
        end_date: str = typer.Option(None, "--end-date", help="Collect until this date (DD-MM-YYYY) — one-off only"),
        brands: str = typer.Option(None, "--brands", help="Brands to track (one-off run)"),
        competitors: str = typer.Option(None, "--competitors", help="Competitors to analyze (one-off run)"),
        hashtags: str = typer.Option(None, "--hashtags", help="Hashtags to track (one-off run)"),
        influencers: str = typer.Option(None, "--influencers", help="Authors/influencers to analyze (one-off run)"),
        topics: str = typer.Option(None, "--topics", help="Topics to analyze (one-off run)"),
    ):
        _run_platform(
            _run_task(
                task=task,
                job_type=job_type,
                sources=sources,
                tracked=tracked,
                keywords=keywords,
                scenario=scenario,
                period=period,
                tenant=tenant,
                force_refresh=force_refresh,
                start_date=start_date,
                end_date=end_date,
                brands=brands,
                competitors=competitors,
                hashtags=hashtags,
                influencers=influencers,
                topics=topics,
            )
        )

    run.__name__ = f"task_{job_type}"
    run.__doc__ = f"Run a {job_type} task now. Use --task <name|id> for an existing task, or pass direct parameters for a one-off run."
    return run


for _jt in ("collect", "digest", "analyze", "prune", "learn", "reflect"):
    task_app.command(name=_jt)(_make_task_run_command(_jt))


app.add_typer(task_app, name="task")

digest_app = typer.Typer(help="Digest operations")


@digest_app.command("send-now")
def digest_send_now(
    period: str = typer.Argument("day", help="Period: day | week"),
    group_by: str = typer.Option("themes", "--group-by", help="Grouping axis: themes | sources | entities | intent | topic_chains"),
    time_breakdown: bool = typer.Option(False, "--time-breakdown", help="Enable per-date sub-entries within each group"),
):
    """Build and publish a digest right now (manual run, not idempotent)."""

    import rich
    from rich import print as rprint

    if period not in ("day", "week"):
        rprint("[red]period must be 'day' or 'week'[/red]")
        raise typer.Exit(1)

    from app.services.digest.builder import DigestDeliveryError, build_and_publish

    async def _run():
        try:
            result = await build_and_publish(period=period, group_by=group_by, time_breakdown=time_breakdown)
        except DigestDeliveryError as e:
            rprint(f"[red]Delivery failed: {e}[/red]")
            raise typer.Exit(1)
        if result.get("text"):
            rich.print(result["text"])
        rprint(f"\n[bold]Status:[/bold] {result.get('status')}")
        if result.get("results"):
            for channel, res in result["results"].items():
                mark = "[green]ok[/green]" if res.get("success") else f"[red]{res.get('error')}[/red]"
                rprint(f"  {channel}: {mark}")

    _run_platform(_run())


app.add_typer(digest_app, name="digest")


@digest_app.command("run")
def digest_run(
    src: str = typer.Option(None, "--src", "-s", help="Source ids, urls or platform (vk/telegram/max)"),
    tenant: str = typer.Option(None, "--tenant", help="Workspace slug or id (empty = all active)"),
    period: str = typer.Option("day", "--period", help="Period: day | week"),
    group_by: str = typer.Option("themes", "--group-by", help="Grouping axis: themes | sources | entities | intent | topic_chains"),
    time_breakdown: bool = typer.Option(False, "--time-breakdown", help="Enable per-date sub-entries within each group"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
):
    """Build and publish a digest directly (not idempotent)."""

    _run_platform(direct._digest_main(period=period, src=src, tenant=tenant, group_by=group_by, time_breakdown=time_breakdown, verbose=verbose))


# app.add_typer(permissions.app, name="permissions", help="Manage permissions")


@app.callback()
def callback():
    """Social Media Manager Administration CLI"""


if __name__ == "__main__":
    app()
