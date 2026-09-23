import typer

from .commands import roles

app = typer.Typer(
    name="SMM Admin CLI",
    help="Social Media Manager CLI",
    no_args_is_help=True,
)

app.add_typer(roles.app, name="roles", help="Manage roles and permissions")


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


schedule_app = typer.Typer(help="Manage cron schedules")


@schedule_app.command("list")
def schedule_list():
    """List all schedules."""

    from rich import print as rprint
    from rich.table import Table

    from app.models import Schedule

    async def _run():
        rows: list[Schedule] = await Schedule.objects.order_by(Schedule.id)  # type: ignore[misc]
        table = Table(title="Schedules")
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


@schedule_app.command("add")
def schedule_add(
    name: str = typer.Argument(..., help="Unique schedule name"),
    cron: str = typer.Argument(..., help="Cron expression, e.g. '0 9 * * *'"),
    job_type: str = typer.Argument(..., help="Job type: collect | digest | prune"),
    payload: str = typer.Option("{}", "--payload", "-p", help="JSON payload, e.g. '{\"source_ids\": [1] }'"),
):
    """Add a schedule."""
    import json as _json

    from rich import print as rprint

    from app.core.config import settings
    from app.models import Schedule
    from app.models.managers.schedule_manager import ScheduleManager
    from app.scheduler.cron import next_run_at

    sm = ScheduleManager()
    if not sm.validate_cron(cron):
        rprint(f"[red]Invalid cron expression: {cron}[/red]")
        raise typer.Exit(1)
    if job_type not in ("collect", "digest", "prune"):
        rprint("[red]job_type must be one of: collect, digest, prune[/red]")
        raise typer.Exit(1)

    async def _run():
        existing = await Schedule.objects.get(name=name)
        if existing:
            rprint(f"[red]Schedule '{name}' already exists[/red]")
            raise typer.Exit(1)
        await sm.create(
            name=name,
            cron_expr=cron,
            timezone=settings.SCHEDULER_TIMEZONE,
            job_type=job_type,
            payload=_json.loads(payload),
            is_active=True,
            next_run_at=next_run_at(cron, settings.SCHEDULER_TIMEZONE),
        )
        rprint(f"[green]Schedule '{name}' created ({cron}, {job_type})[/green]")

    _run_platform(_run())


@schedule_app.command("remove")
def schedule_remove(name: str = typer.Argument(...)):
    """Remove a schedule by name."""

    from rich import print as rprint

    from app.models import Schedule

    async def _run():
        deleted = await Schedule.objects.delete(name=name)
        rprint(f"[green]Deleted {deleted} schedule(s)[/green]" if deleted else "[yellow]Not found[/yellow]")

    _run_platform(_run())


@schedule_app.command("pause")
def schedule_pause(name: str = typer.Argument(...), resume: bool = typer.Option(False, "--resume")):
    """Pause (or --resume) a schedule."""

    from rich import print as rprint

    from app.models import Schedule

    async def _run():
        s: Schedule | None = await Schedule.objects.get(name=name)
        if not s:
            rprint("[yellow]Not found[/yellow]")
            raise typer.Exit(1)
        await Schedule.objects.update_by_id(s.id, is_active=resume)
        rprint(f"[green]{'Resumed' if resume else 'Paused'} '{name}'[/green]")

    _run_platform(_run())


app.add_typer(schedule_app, name="schedule")

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
