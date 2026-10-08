"""Manage topic chains: inspect, deduplicate, merge.

These commands operate on ``ai_analytics`` rows and ``topic_chain_id`` /
``normalized_label``.  Use ``--apply`` to write changes; without it the command
runs in dry-run mode and only prints what would happen.
"""
from __future__ import annotations

import argparse

import typer
from rich import print as rprint
from rich.table import Table

app = typer.Typer(name="chains", help="Manage topic chains")


def _run(coro):
    import asyncio

    from app.core.tenant_context import tenant_scope

    with tenant_scope(bypass=True):
        return asyncio.run(coro)


@app.command("merge")
def chains_merge(apply: bool = typer.Option(False, "--apply", help="Write changes (default: dry-run)")):
    """Merge duplicate topic chains into the oldest one.

    Duplicates are detected by ``normalized_label`` (lowercase, ё→е, stemmed).
    All rows with the same ``normalized_label`` are moved to the oldest chain
    in that group.
    """
    from scripts.merge_duplicate_chains import merge_duplicates

    updated = _run(merge_duplicates(apply=apply))
    if apply:
        rprint(f"[green]Updated {updated} row(s)[/green]")
    else:
        rprint(f"[yellow]Dry-run: {updated} row(s) would be updated (pass --apply to write)[/yellow]")


@app.command("groups")
def chains_groups(min_size: int = typer.Option(2, "--min-size", help="Only show groups with at least N rows")):
    """Show duplicate normalized_label groups (read-only)."""

    async def _list():
        from collections import defaultdict

        from app.core.database import async_session_maker
        from sqlalchemy import select

        from app.models import AIAnalytics

        rows = (
            await async_session_maker().execute(
                select(AIAnalytics.id, AIAnalytics.normalized_label, AIAnalytics.topic_chain_id, AIAnalytics.chain_label)
                .where(AIAnalytics.normalized_label.isnot(None))
                .where(AIAnalytics.normalized_label != "")
            )
        ).all()

        groups: dict[str, list] = defaultdict(list)
        for row_id, label, chain_id, chain_label in rows:
            if label:
                groups[label].append((row_id, chain_id, chain_label))

        table = Table(title="Normalized label groups")
        for col in ("normalized_label", "rows", "canonical_chain_id", "chain_label"):
            table.add_column(col)

        shown = 0
        for label, items in sorted(groups.items()):
            if len(items) < min_size:
                continue
            canonical = sorted(items, key=lambda x: x[0])[0][1]
            c_label = next((cl for _, cid, cl in items if cid == canonical), "")
            table.add_row(label[:80], str(len(items)), str(canonical), str(c_label)[:60])
            shown += 1

        rprint(table)
        rprint(f"[dim]{shown} group(s) with >= {min_size} rows[/dim]")

    _run(_list())
