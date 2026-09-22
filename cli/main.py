# cli/main.py
import typer

from .commands import roles

app = typer.Typer(
    name="SMM Admin CLI",
    help="Social Media Manager CLI",
    no_args_is_help=True,
)

app.add_typer(roles.app, name="roles", help="Manage roles and permissions")

schedule_app = typer.Typer(help="Manage cron schedules")


@schedule_app.command("list")
def schedule_list():
    """List all schedules."""
    import asyncio

    import rich

    from app.models import Schedule

    async def _run():
        rows = await Schedule.objects.order_by(Schedule.id)
        table = rich.table.Table(title="Schedules")
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
        rich.print(table)

    asyncio.run(_run())


@schedule_app.command("add")
def schedule_add(
    name: str = typer.Argument(..., help="Unique schedule name"),
    cron: str = typer.Argument(..., help="Cron expression, e.g. '0 9 * * *'"),
    job_type: str = typer.Argument(..., help="Job type: collect | digest | prune"),
    payload: str = typer.Option("{}", "--payload", "-p", help="JSON payload, e.g. '{\"source_ids\": [1] }'"),
):
    """Add a schedule."""
    import asyncio
    import json as _json

    import rich

    from app.core.config import settings
    from app.models import Schedule
    from app.models.managers.schedule_manager import ScheduleManager
    from app.scheduler.cron import next_run_at

    sm = ScheduleManager()
    if not sm.validate_cron(cron):
        rich.print(f"[red]Invalid cron expression: {cron}[/red]")
        raise typer.Exit(1)
    if job_type not in ("collect", "digest", "prune"):
        rich.print("[red]job_type must be one of: collect, digest, prune[/red]")
        raise typer.Exit(1)

    async def _run():
        existing = await Schedule.objects.get(name=name)
        if existing:
            rich.print(f"[red]Schedule '{name}' already exists[/red]")
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
        rich.print(f"[green]Schedule '{name}' created ({cron}, {job_type})[/green]")

    asyncio.run(_run())


@schedule_app.command("remove")
def schedule_remove(name: str = typer.Argument(...)):
    """Remove a schedule by name."""
    import asyncio

    import rich

    from app.models import Schedule

    async def _run():
        deleted = await Schedule.objects.delete(name=name)
        rich.print(f"[green]Deleted {deleted} schedule(s)[/green]" if deleted else "[yellow]Not found[/yellow]")

    asyncio.run(_run())


@schedule_app.command("pause")
def schedule_pause(name: str = typer.Argument(...), resume: bool = typer.Option(False, "--resume")):
    """Pause (or --resume) a schedule."""
    import asyncio

    import rich

    from app.models import Schedule

    async def _run():
        s = await Schedule.objects.get(name=name)
        if not s:
            rich.print("[yellow]Not found[/yellow]")
            raise typer.Exit(1)
        await Schedule.objects.update_by_id(s.id, is_active=resume)
        rich.print(f"[green]{'Resumed' if resume else 'Paused'} '{name}'[/green]")

    asyncio.run(_run())


app.add_typer(schedule_app, name="schedule")
# app.add_typer(permissions.app, name="permissions", help="Manage permissions")


@app.callback()
def callback():
    """Social Media Manager Administration CLI"""


if __name__ == "__main__":
    app()
