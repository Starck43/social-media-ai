"""Backfill and merge duplicate topic chains.

Groups existing ``ai_analytics`` rows by ``normalized_label``, then merges
each duplicate group into the oldest chain.  Updates ``topic_chain_id`` and
``chain_label`` on all affected rows so the dashboard/digest grouping becomes
consistent without manual SQL.

Idempotent: only touches rows where ``normalized_label IS NOT NULL`` and the
current ``topic_chain_id`` is not the target (oldest) chain.

Usage:
    python -m scripts.merge_duplicate_chains              # dry-run (default)
    python -m scripts.merge_duplicate_chains --apply       # write changes
"""
import argparse
import logging
import sys
from pathlib import Path

project_root = str(Path(__file__).parent.parent)
sys.path.append(project_root)

from collections import defaultdict
from sqlalchemy import select, func

from app.core.config import settings
from app.core.database import async_session_maker
from app.models import AIAnalytics
from app.services.ai.chain_resolver import _normalize

logger = logging.getLogger(__name__)


async def find_duplicate_groups(dry_run: bool = True) -> dict[str, list[int]]:
    """Return {normalized_label: [analysis_ids]} for rows with duplicates."""
    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(AIAnalytics.id, AIAnalytics.normalized_label, AIAnalytics.topic_chain_id)
                .where(AIAnalytics.normalized_label.isnot(None))
                .where(AIAnalytics.normalized_label != "")
            )
        ).all()

    groups: dict[str, list[int]] = defaultdict(list)
    for row_id, label, chain_id in rows:
        if label:
            groups[label].append((row_id, chain_id))

    duplicates = {label: [rid for rid, _ in items] for label, items in groups.items() if len(items) > 1}
    logger.info("Found %d duplicate normalized_label groups (%s)", len(duplicates), "dry-run" if dry_run else "apply")
    return duplicates


async def merge_duplicates(apply: bool = False) -> int:
    """Merge duplicate chains into the oldest one.

    Returns number of rows updated.
    """
    groups = await find_duplicate_groups(dry_run=not apply)
    if not groups:
        logger.info("Nothing to merge.")
        return 0

    updated = 0
    async with async_session_maker() as session:
        for label, row_ids in groups.items():
            # Pick the oldest row as the canonical chain
            canonical_id = (
                await session.execute(
                    select(AIAnalytics.topic_chain_id)
                    .where(AIAnalytics.id.in_(row_ids))
                    .order_by(AIAnalytics.analysis_date.asc(), AIAnalytics.id.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            if not canonical_id:
                continue

            canonical_label = (
                await session.execute(
                    select(AIAnalytics.chain_label)
                    .where(AIAnalytics.topic_chain_id == canonical_id)
                    .order_by(AIAnalytics.analysis_date.desc(), AIAnalytics.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            target_label = canonical_label or label

            rows_to_update = (
                await session.execute(
                    select(AIAnalytics).where(
                        AIAnalytics.id.in_(row_ids),
                        AIAnalytics.topic_chain_id != canonical_id,
                    )
                )
            ).scalars().all()

            if not rows_to_update:
                continue

            logger.info(
                "Group %r: %d rows -> canonical chain %r (label=%r)",
                label[:60], len(rows_to_update), canonical_id, target_label,
            )

            if apply:
                for row in rows_to_update:
                    row.topic_chain_id = canonical_id
                    row.chain_label = target_label
                await session.commit()
                updated += len(rows_to_update)
            else:
                for row in rows_to_update:
                    logger.info("  dry-run: row %s would move to %r", row.id, canonical_id)
                updated += len(rows_to_update)

    logger.info("Total rows %s: %d", "updated" if apply else "to update", updated)
    return updated


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Merge duplicate topic chains")
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    args = parser.parse_args()

    import asyncio
    asyncio.run(merge_duplicates(apply=args.apply))
