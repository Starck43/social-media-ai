"""Manual content collection & analysis via CLI.

Replaces the legacy `cli/scheduler.py` debug script (now removed). Reuses the
same `ContentCollector.collect_from_source` pipeline the runtime jobs use, so
the analyzer behavior you test here is exactly what the cron/agent path runs.
Rich console output is kept as the debug value (live progress + summaries).

Run as: python -m cli.main collect run ...
"""

from __future__ import annotations

from typing import Optional

import typer

app = typer.Typer(name="collect", help="Run manual content collection & analysis")


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


@app.command("run")
def run_collect(
    source_id: Optional[int] = typer.Option(None, "--source-id", help="Collect from a specific source ID"),
    source_url: Optional[str] = typer.Option(None, "--source-url", help="Collect by source URL (external_id)"),
    platform_id: Optional[int] = typer.Option(None, "--platform-id", help="Collect all active sources on a platform"),
    start_date: Optional[str] = typer.Option(None, "--start-date", help="Start date DD-MM-YYYY (with --force-refresh)"),
    end_date: Optional[str] = typer.Option(None, "--end-date", help="End date DD-MM-YYYY (with --force-refresh)"),
    force_refresh: bool = typer.Option(
        False, "--force-refresh", help="Reset analytics + last_checked, full re-analysis"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed collection output"),
):
    """Run content collection & AI analysis manually (debug/analyst tool)."""
    from rich import print as rprint
    from rich.panel import Panel
    from rich.table import Table

    from app.models import AIAnalytics, Source
    from app.services.monitoring.collector import ContentCollector

    parsed_start = _parse_date(start_date)
    parsed_end = _parse_date(end_date)

    async def _run_all():
        sources: list[Source] = []
        if source_id:
            src = await Source.objects.get(id=source_id)
            sources = [src] if src else []
        elif source_url:
            external_id = source_url.rstrip("/").split("/")[-1]
            qs = Source.objects.filter(external_id=external_id, is_active=True)
            if platform_id:
                qs = qs.filter(platform_id=platform_id)
            sources = list(await qs)
        elif platform_id:
            sources = list(await Source.objects.filter(platform_id=platform_id, is_active=True))
        else:
            sources = list(await Source.objects.filter(is_active=True))

        if not sources:
            rprint("[red]No active sources matched the given filters[/red]")
            raise typer.Exit(1)

        if force_refresh:
            deleted = 0
            for src in sources:
                qs = AIAnalytics.objects.filter(source_id=src.id)
                if parsed_start:
                    qs = qs.filter(analysis_date__gte=parsed_start)
                if parsed_end:
                    qs = qs.filter(analysis_date__lte=parsed_end)
                rows = await qs.all()
                if rows:
                    await AIAnalytics.objects.filter(id__in=[r.id for r in rows]).delete()
                    deleted += len(rows)
                await Source.objects.update_by_id(src.id, last_checked=None)
            rprint(f"[yellow]Force refresh: deleted {deleted} analytics record(s), reset last_checked[/yellow]")

        rprint(
            Panel.fit(
                "[bold cyan]🚀 STARTING CONTENT COLLECTION[/bold cyan]",
                border_style="cyan",
            )
        )

        collector = ContentCollector()
        stats = {"sources": 0, "collected": 0, "failed": 0, "items": 0, "analytics": 0}

        for source in sources:
            # CLI mode: full collection with date filtering (incremental_mode off).
            params = dict(source.params or {})
            params.pop("incremental_mode", None)
            if parsed_start or parsed_end:
                params["force_refresh"] = True
                params["cli_dates"] = {
                    "start_date": parsed_start,
                    "end_date": parsed_end,
                }
            source.params = params

            if verbose:
                scenario = source.agent_scenario
                analyze_by = scenario.analyze_type if scenario else "themes"
                rprint(f"[dim]🎯 Source: {source.name} (id={source.id}, platform={source.platform.name})[/dim]")
                rprint(f"[dim]   Scenario: {scenario.name if scenario else 'None'} (analyze by: {analyze_by})[/dim]")
                rprint(f"[dim]   Last checked: {source.last_checked}[/dim]")

            stats["sources"] += 1
            try:
                result = await collector.collect_from_source(source)
                if result:
                    stats["collected"] += 1
                    stats["items"] += result.get("content_count", 0)
                    stats["analytics"] += result.get("analytics_count", 0)
                    if verbose:
                        rprint(
                            f"[green]  ✅ collected {result['content_count']} items, "
                            f"{result.get('analytics_count', 0)} analytics[/green]"
                        )
                else:
                    stats["failed"] += 1
                    if verbose:
                        rprint(f"[red]  ❌ failed for source {source.id}[/red]")
            except Exception as e:
                stats["failed"] += 1
                rprint(f"[red]  ❌ error for source {source.id}: {e}[/red]")

        return stats

    stats = _run(_run_all())

    table = Table(show_header=True, header_style="bold blue")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")
    for k, label in (
        ("sources", "Sources"),
        ("collected", "Successful"),
        ("failed", "Failed"),
        ("items", "Items Collected"),
        ("analytics", "Analytics Created"),
    ):
        table.add_row(label, str(stats[k]))

    rprint(
        Panel.fit(
            table,
            title="[bold green]📊 COLLECTION COMPLETE[/bold green]",
            border_style="green",
        )
    )
