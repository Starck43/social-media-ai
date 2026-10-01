"""Direct content collection via CLI.

A thin wrapper over the `collect` job handler: resolves `--src`, builds the
handler payload and runs it now (no task, no queue). The handler runs the same
`ContentCollector` pipeline the runtime uses, so what you test here is exactly
what the cron/agent path runs.

Run as: python -m cli.main collect --src 739,740 [--tenant ...]
"""

from __future__ import annotations

from typing import Optional

import typer
from rich import print as rprint
from rich.panel import Panel
from rich.table import Table

from cli.run import resolve_sources, run_handler


def _run(coro):
    """Run an async command as the platform owner (bypass tenant scope)."""
    import asyncio

    from app.core.tenant_context import tenant_scope

    with tenant_scope(bypass=True):
        return asyncio.run(coro)


def _parse_date(value: Optional[str]):
    """Parse a CLI date string (DD-MM-YYYY / DD.MM.YYYY / ISO) to a date object."""
    from app.utils.date_parsing import universal_date_parser

    if not value:
        return None
    parsed = universal_date_parser(value)
    if parsed is None:
        raise typer.BadParameter(f"Invalid date: {value!r}. Use DD-MM-YYYY (e.g. 01-05-2025)")
    return parsed.date()


def _report(stats: dict) -> None:
    table = Table(show_header=True, header_style="bold blue")
    table.add_column("Метрика", style="cyan")
    table.add_column("Значение", style="white")
    for k, label in (
        ("sources", "Источников"),
        ("collected", "Собрано"),
        ("empty", "Без контента"),
        ("error", "Ошибки"),
        ("items", "Записей собрано"),
        ("analytics", "Аналитики создано"),
        ("excluded", "Исключено"),
    ):
        table.add_row(label, str(stats.get(k, 0)))
    rprint(Panel.fit(table, title="[bold green]📊 СБОР ЗАВЕРШЁН[/bold green]", border_style="green"))
    if stats.get("empty"):
        rprint("[dim]Без контента = источник опрошен, но записей нет (это не ошибка).[/dim]")
    if stats.get("error_messages"):
        rprint("[bold red]Ошибки при сборе:[/bold red]")
        for msg in stats["error_messages"]:
            rprint(f"  [red]• {msg}[/red]")


def collect_cmd(
    src: str = typer.Option(None, "--src", "-s", help="Source ids, urls or platform (vk/telegram/max), comma/space separated"),
    tenant: str = typer.Option(None, "--tenant", help="Workspace slug or id (empty = all active sources)"),
    monitored: str = typer.Option(None, "--monitored", help="Usernames to collect for instead of source defaults"),
    excluded: str = typer.Option(None, "--excluded", help="Usernames to skip"),
    start_date: str = typer.Option(None, "--start-date", help="Start date DD-MM-YYYY (with --force-refresh)"),
    end_date: str = typer.Option(None, "--end-date", help="End date DD-MM-YYYY (with --force-refresh)"),
    force_refresh: bool = typer.Option(False, "--force-refresh", help="Reset analytics + last_checked, full re-analysis"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed collection output"),
):
    """Run content collection & AI analysis manually (debug/analyst tool)."""
    from cli._tenant import resolve_tenant_id

    async def _main():
        tenant_id = await resolve_tenant_id(tenant)
        sources = await resolve_sources(src, tenant_id)
        if not sources:
            rprint("[red]No active sources matched the given filters[/red]")
            raise typer.Exit(1)

        payload = {"source_ids": [s.id for s in sources]}
        if monitored:
            payload["monitored_users"] = [
                m.strip().lstrip("@") for m in monitored.replace(",", " ").split() if m.strip()
            ]
        if excluded:
            payload["excluded_users"] = [
                e.strip().lstrip("@") for e in excluded.replace(",", " ").split() if e.strip()
            ]
        if force_refresh or start_date or end_date:
            payload["force_refresh"] = True
            cli_dates = {}
            if start_date:
                cli_dates["start_date"] = _parse_date(start_date)
            if end_date:
                cli_dates["end_date"] = _parse_date(end_date)
            if cli_dates:
                payload["cli_dates"] = cli_dates

        rprint(Panel.fit("[bold cyan]🚀 НАЧАЛО СБОРА КОНТЕНТА[/bold cyan]", border_style="cyan"))
        if verbose:
            for s in sources:
                scenario = s.agent_scenario
                rprint(
                    f"[dim]🎯 Источник: {s.name} (id={s.id}, platform={s.platform.name}, "
                    f"scenario={scenario.name if scenario else 'None'})[/dim]"
                )

        return await run_handler("collect", payload, tenant_id)

    stats = _run(_main())
    _report(stats)
