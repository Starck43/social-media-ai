"""Backfill human-readable chain labels for old rows.

Rows written before migration 0081 (add `chain_label`) have
`chain_label = NULL`, topics stored under
`summary_data.multi_llm_analysis.text_analysis.main_topics` (not under
`parsed`), and `topic_chain_id` generated from the old deterministic scheme
(e.g. `src_2_scn_6_память`). This script derives a label from each row's own
`summary_data` via the same helper the UI uses (`human_chain_label`) and
writes it to `chain_label`, so the dashboard stops showing raw chain ids.

Idempotent: only touches rows where `chain_label IS NULL`.
Run with `python -m scripts.backfill_chain_labels`.
"""
import logging
import sys
from pathlib import Path

project_root = str(Path(__file__).parent.parent)
sys.path.append(project_root)

from sqlalchemy import select, func

from app.core.config import settings
from app.core.database import async_session_maker
from app.models import AIAnalytics
from app.services.ai.chain_resolver import human_chain_label

logger = logging.getLogger(__name__)


async def backfill() -> int:
    """Derive and store `chain_label` for rows that have none yet."""
    updated = 0
    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(AIAnalytics).where(
                    AIAnalytics.topic_chain_id.isnot(None),
                    AIAnalytics.chain_label.is_(None),
                )
            )
        ).scalars().all()

        for row in rows:
            label = human_chain_label(row.summary_data)
            if not label:
                logger.info("row %s: no label derivable, leaving as-is", row.id)
                continue
            row.chain_label = label
            updated += 1
        await session.commit()
    logger.info("backfilled %d row(s) with chain_label", updated)
    return updated


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import asyncio

    asyncio.run(backfill())
