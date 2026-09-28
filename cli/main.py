import typer

from .commands import collect, credentials, roles, scenarios

app = typer.Typer(
    name="SMM Admin CLI",
    help="Social Media Manager CLI",
    no_args_is_help=True,
)

app.add_typer(roles.app, name="roles", help="Manage roles and permissions")
app.add_typer(credentials.app, name="credentials", help="Manage platform credentials (tenant vault)")
app.add_typer(scenarios.app, name="scenarios", help="Manage agent scenarios")
app.add_typer(collect.app, name="collect", help="Run manual content collection & analysis")


def _run_platform(coro):
    """Run an async command as the platform owner (bootstrap workspace).

    The CLI is the operator's tool: it administers the owner workspace and, with
    an explicit `--tenant`, a client one. Tenant-scoped rows cannot be read or
    written without a scope, so every command goes through here.
    """
    import asyncio

    from app.core.tenant_context import tenant_scope

    with tenant_scope(bypass=True):
        return asyncio.run(coro)


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


async def _set_task_sources(task_id: int, source_ids: list[int]) -> None:
    """Link a task to the given sources via the m2m table."""
    from app.core.database import async_session_maker
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models.agent_task import agent_task_sources

    if not source_ids:
        return
    async with async_session_maker() as session:
        stmt = pg_insert(agent_task_sources).values(
            [{"agent_task_id": task_id, "source_id": sid} for sid in source_ids]
        )
        await session.execute(stmt.on_conflict_do_nothing())
        await session.commit()


task_app = typer.Typer(help="Manage agent tasks")


@task_app.command("list")
def task_list():
    """List all tasks."""

    from rich import print as rprint
    from rich.table import Table

    from app.models import AgentTask

    async def _run():
        rows: list[AgentTask] = await AgentTask.objects.order_by(AgentTask.id)  # type: ignore[misc]
        table = Table(title="AgentTasks")
        for col in ("id", "name", "cron", "job", "active", "next_run_at", "last_status"):
            table.add_column(col)
        for s in rows:
            table.add_row(
                str(s.id),
                s.name,
                s.cron_expr,
                s.job_type,
                "yes" if s.is_active else "no",
                s.next_run_at.strftime("%Y-%m-%d %H:%M") if s.next_run_at else "—",
                s.last_status or "—",
            )
        rprint(table)

    _run_platform(_run())


@task_app.command("add")
def task_add(
    name: str = typer.Argument(..., help="Unique task name"),
    cron: str = typer.Argument(..., help="Cron expression, e.g. '0 9 * * *' (or @once)"),
    job_type: str = typer.Argument(..., help="Job type: collect | digest | prune | analyze | learn | reflect"),
    source_ids: str = typer.Option(
        None, "--sources", "-s", help="Comma/space separated source IDs to link (empty = all active)"
    ),
    monitored_users: str = typer.Option(
        None, "--monitored", help="Comma/space separated usernames to collect for (collect only)"
    ),
    excluded_users: str = typer.Option(
        None, "--excluded", help="Comma/space separated usernames to skip (collect/analyze)"
    ),
    scenario_id: int = typer.Option(None, "--scenario", help="AgentScenario ID to apply when the task runs"),
    payload: str = typer.Option("{}", "--payload", "-p", help="Extra JSON payload, e.g. '{\"period\": \"week\"}'"),
):
    """Add a task."""
    import json as _json

    from rich import print as rprint

    from app.core.config import settings
    from app.models import AgentTask
    from app.models.managers.agent_task_manager import AgentTaskManager
    from app.tasks.cron import next_run_at

    sm = AgentTaskManager()
    if not sm.validate_cron(cron):
        rprint(f"[red]Invalid cron expression: {cron}[/red]")
        raise typer.Exit(1)
    from app.jobs.handlers import HANDLERS

    if job_type not in HANDLERS:
        rprint(f"[red]job_type must be one of: {', '.join(HANDLERS.keys())}[/red]")
        raise typer.Exit(1)

    parsed_payload = _json.loads(payload)
    if monitored_users:
        parsed_payload["monitored_users"] = _split_names(monitored_users)
    if excluded_users:
        parsed_payload["excluded_users"] = _split_names(excluded_users)
    parsed_source_ids = _split_ints(source_ids)

    async def _run():
        existing = await AgentTask.objects.get(name=name)
        if existing:
            rprint(f"[red]AgentTask '{name}' already exists[/red]")
            raise typer.Exit(1)
        task = await sm.create(
            name=name,
            cron_expr=cron,
            timezone=settings.SCHEDULER_TIMEZONE,
            job_type=job_type,
            payload=parsed_payload,
            agent_scenario_id=scenario_id,
            is_active=True,
            next_run_at=next_run_at(cron, settings.SCHEDULER_TIMEZONE),
        )
        if parsed_source_ids:
            await _set_task_sources(task.id, parsed_source_ids)
        rprint(f"[green]AgentTask '{name}' created ({cron}, {job_type})[/green]")

    _run_platform(_run())


@task_app.command("remove")
def task_remove(name: str = typer.Argument(...)):
    """Remove a task by name."""

    from rich import print as rprint

    from app.models import AgentTask

    async def _run():
        deleted = await AgentTask.objects.delete(name=name)
        rprint(f"[green]Deleted {deleted} task(s)[/green]" if deleted else "[yellow]Not found[/yellow]")

    _run_platform(_run())


@task_app.command("pause")
def task_pause(name: str = typer.Argument(...), resume: bool = typer.Option(False, "--resume")):
    """Pause (or --resume) a task."""

    from rich import print as rprint

    from app.models import AgentTask

    async def _run():
        s: AgentTask | None = await AgentTask.objects.get(name=name)
        if not s:
            rprint("[yellow]Not found[/yellow]")
            raise typer.Exit(1)
        await AgentTask.objects.update_by_id(s.id, is_active=resume)
        rprint(f"[green]{'Resumed' if resume else 'Paused'} '{name}'[/green]")

    _run_platform(_run())


@task_app.command("run")
def task_run(
    task: str = typer.Option(None, "--task", "-t", help="Existing task by name or id to run"),
    job_type: str = typer.Option("collect", "--job-type", help="Job type for a one-off run (default collect)"),
    sources: str = typer.Option(None, "--sources", "-s", help="Source IDs to link (one-off run)"),
    monitored: str = typer.Option(None, "--monitored", help="Usernames to collect (one-off run)"),
    excluded: str = typer.Option(None, "--excluded", help="Usernames to skip (one-off run)"),
    scenario: int = typer.Option(None, "--scenario", help="AgentScenario ID (one-off run)"),
    period: str = typer.Option(None, "--period", help="Period for digest/collect: day | week | last month etc."),
):
    """Run a job now. Use --task <name|id> for an existing task, or pass
    direct parameters to create and run a one-off @once task."""
    import json as _json

    from rich import print as rprint

    from app.core.config import settings
    from app.jobs.dispatcher import drain
    from app.models.managers.agent_task_manager import AgentTaskManager
    from app.models.managers.job_manager import JobManager

    async def _run():
        from datetime import datetime, timezone

        from app.models import AgentTask
        from app.tasks.cron import next_run_at

        if task:
            target = None
            if task.isdigit():
                target = await AgentTask.objects.get(id=int(task))
            if target is None:
                target = await AgentTask.objects.get(name=task)
            if target is None:
                rprint(f"[red]Task '{task}' not found[/red]")
                raise typer.Exit(1)
            await JobManager().enqueue(
                job_type=target.job_type,
                payload=target.payload or {},
                agent_task_id=target.id,
                run_at=datetime.now(timezone.utc),
            )
            rprint(f"[green]Job enqueued for task '{target.name}'[/green]")
        else:
            payload: dict = {}
            if monitored:
                payload["monitored_users"] = _split_names(monitored)
            if excluded:
                payload["excluded_users"] = _split_names(excluded)
            if period:
                payload["period"] = period
            task_obj = await AgentTask.objects.create(
                name=f"one-off-{job_type}",
                cron_expr="@once",
                timezone=settings.SCHEDULER_TIMEZONE,
                job_type=job_type,
                payload=payload,
                agent_scenario_id=scenario,
                is_active=True,
                next_run_at=datetime.now(timezone.utc),
            )
            if _split_ints(sources):
                await _set_task_sources(task_obj.id, _split_ints(sources))
            await JobManager().enqueue(
                job_type=job_type,
                payload=payload,
                agent_task_id=task_obj.id,
                run_at=datetime.now(timezone.utc),
            )
            rprint(f"[green]One-off job enqueued ({job_type})[/green]")

        processed = await drain(max_jobs=1)
        rprint(f"[bold]Processed {processed} job(s).[/bold]")

    _run_platform(_run())


app.add_typer(task_app, name="task")

digest_app = typer.Typer(help="Digest operations")


@digest_app.command("send-now")
def digest_send_now(
    period: str = typer.Argument("day", help="Period: day | week"),
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
            result = await build_and_publish(period=period)
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


# app.add_typer(permissions.app, name="permissions", help="Manage permissions")


@app.callback()
def callback():
    """Social Media Manager Administration CLI"""


if __name__ == "__main__":
    app()
