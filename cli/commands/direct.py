"""Direct CLI commands for the non-collect job types.

Each command is a thin wrapper over its job handler: resolve `--src`, build the
handler payload, run it now (no task, no queue). They mirror `collect` so that
every job type has the same shape of direct, debug/analyst entry point:

    python -m cli.main analyze --src 739 --scenario 3
    python -m cli.main digest --src 739 --period week
    python -m cli.main prune --src 739 --days 14
    python -m cli.main learn  --src 739 --min-messages 5
    python -m cli.main reflect --src 739

Registration happens in `cli/main.py` so every command lands directly on the
top-level CLI app (`cli.main analyze`, not `cli.main direct ...`).
"""

from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich import print as rprint
from rich.panel import Panel
from rich.table import Table

from cli._inputs import parse_usernames
from cli.run import resolve_sources, run_handler


def _sync(coro) -> object:
    """Run an async command as the platform owner (bypass tenant scope)."""
    from app.core.tenant_context import tenant_scope

    with tenant_scope(bypass=True):
        return asyncio.run(coro())


def _report(title: str, stats: dict, colour: str = "green") -> None:
    table = Table(show_header=True, header_style="bold blue")
    table.add_column("Метрика", style="cyan")
    table.add_column("Значение", style="white")
    for k, v in (stats or {}).items():
        if k == "status":
            continue
        table.add_row(str(k), str(v))
    rprint(Panel.fit(table, title=f"[bold {colour}]📊 {title}[/bold {colour}]", border_style=colour))


def _resolve(tenant: str | None, src: str | None, **extra):
    """Build the handler payload for the resolved sources. Returns (tenant_id, payload)."""
    from cli._tenant import resolve_tenant_id

    async def _main():
        tenant_id = await resolve_tenant_id(tenant)
        sources = await resolve_sources(src, tenant_id)
        if not sources:
            rprint("[red]Не найдено активных источников по заданным фильтрам[/red]")
            raise typer.Exit(1)
        payload = {"source_ids": [s.id for s in sources]}
        payload.update({k: v for k, v in extra.items() if v is not None})
        return tenant_id, payload, sources

    return _main


# --- analyze ---------------------------------------------------------------


def cmd_analyze(
    src: str = typer.Option(None, "--src", "-s", help="Source ids, urls or platform (vk/telegram/max)"),
    tenant: str = typer.Option(None, "--tenant", help="Workspace slug or id (empty = all active)"),
    scenario: int = typer.Option(None, "--scenario", help="AgentScenario ID to apply"),
    excluded: str = typer.Option(None, "--excluded", help="Usernames to skip"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
):
    """Run the analyze job handler (trigger evaluation + bot actions) directly."""
    excluded_users = parse_usernames(excluded)

    async def _main():
        tenant_id, payload, sources = await _resolve(tenant, src, scenario_id=scenario, excluded_users=excluded_users)
        if verbose:
            for s in sources:
                rprint(f"[dim]🎯 Источник: {s.name} (id={s.id}, platform={s.platform.name})[/dim]")
        return await run_handler("analyze", payload, tenant_id)

    stats = _sync(_main())
    _report("АНАЛИЗ ЗАВЕРШЁН", stats)
    return stats


# --- digest ----------------------------------------------------------------


def cmd_digest(
    src: str = typer.Option(None, "--src", "-s", help="Source ids, urls or platform (vk/telegram/max)"),
    tenant: str = typer.Option(None, "--tenant", help="Workspace slug or id (empty = all active)"),
    period: str = typer.Option("day", "--period", help="Period: day | week"),
    group_by: str = typer.Option("themes", "--group-by", help="Grouping axis: themes | sources | entities | intent | topic_chains"),
    time_breakdown: bool = typer.Option(False, "--time-breakdown", help="Enable per-date sub-entries within each group"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
):
    """Build and publish a digest directly (not idempotent)."""
    stats = _sync(_digest_main(period=period, src=src, tenant=tenant, group_by=group_by, time_breakdown=time_breakdown, verbose=verbose))
    _report("ДАЙДЖЕСТ ЗАВЕРШЁН", stats)
    return stats


async def _digest_main(*, period: str, src: str | None, tenant: str | None, group_by: str = "themes", time_breakdown: bool = False, verbose: bool = False):
    """Shared async body for the digest direct command."""
    tenant_id, payload, sources = await _resolve(tenant, src, period=period, group_by=group_by, time_breakdown=time_breakdown)
    if verbose:
        for s in sources:
            rprint(f"[dim]🎯 Источник: {s.name} (id={s.id}, platform={s.platform.name})[/dim]")
    return await run_handler("digest", payload, tenant_id)


# --- prune -----------------------------------------------------------------


def cmd_prune(
    src: str = typer.Option(None, "--src", "-s", help="Source ids, urls or platform (vk/telegram/max)"),
    tenant: str = typer.Option(None, "--tenant", help="Workspace slug or id (empty = all active)"),
    days: int = typer.Option(7, "--days", help="Retain jobs newer than this many days"),
):
    """Trim old finished jobs directly."""
    async def _main():
        tenant_id, payload, _sources = await _resolve(tenant, src, days=days)
        return await run_handler("prune", payload, tenant_id)

    stats = _sync(_main())
    _report("ОЧИСТКА ЗАВЕРШЕНА", stats)
    return stats


# --- learn -----------------------------------------------------------------


def cmd_learn(
    src: str = typer.Option(None, "--src", "-s", help="Source ids, urls or platform (vk/telegram/max)"),
    tenant: str = typer.Option(None, "--tenant", help="Workspace slug or id (empty = all active)"),
    min_messages: int = typer.Option(8, "--min-messages", help="New user turns required before an LLM call"),
    window: int = typer.Option(200, "--window", help="Max messages to scan per run"),
):
    """Extract durable facts from recent chat into memory directly."""
    async def _main():
        tenant_id, payload, _sources = await _resolve(tenant, src, min_messages=min_messages, window=window)
        return await run_handler("learn", payload, tenant_id)

    stats = _sync(_main())
    _report("ОБУЧЕНИЕ ЗАВЕРШЕНО", stats)
    return stats


# --- reflect ---------------------------------------------------------------


def cmd_reflect(
    src: str = typer.Option(None, "--src", "-s", help="Source ids, urls or platform (vk/telegram/max)"),
    tenant: str = typer.Option(None, "--tenant", help="Workspace slug or id (empty = all active)"),
    dedup: bool = typer.Option(True, "--dedup/--no-dedup", help="Apply delete/update ops proposed by the LLM"),
):
    """Weekly memory hygiene + prompt-evolution proposals directly."""
    async def _main():
        tenant_id, payload, _sources = await _resolve(tenant, src, dedup=dedup)
        return await run_handler("reflect", payload, tenant_id)

    stats = _sync(_main())
    _report("РЕФЛЕКСИЯ ЗАВЕРШЕНА", stats)
    return stats
