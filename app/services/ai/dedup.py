"""Content deduplication for AI analysis.

A repeated item must never be analyzed (and paid for) twice. Two levels:

1. **Batch** — the exact same batch of items for a source maps to
   ``ai_analytics.content_hash`` (sha256 of the sorted per-item hashes);
   the existing row is returned without calling any LLM.
2. **Item** — every analyzed item's hash is stored in
   ``summary_data["content_hashes"]``, so overlapping batches only pay for
   the genuinely new items.

Hashes are deliberately built from stable identity + text only (no counters,
no dates): engagement metrics change between fetches and must not defeat dedup.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# How far back to look for already-analyzed content.
LOOKBACK_DAYS = 30
# Safety cap so a noisy source cannot load unbounded history into memory.
MAX_ANALYSES = 200

_HASH_SEP = "\x1f"


def item_hash(item: dict) -> str:
    """Stable sha256 of one normalized content item."""
    parts = [
        str(item.get("platform") or ""),
        str(item.get("external_id") or item.get("id") or ""),
        (item.get("text") or "").strip(),
    ]
    return hashlib.sha256(_HASH_SEP.join(parts).encode("utf-8")).hexdigest()


def hashes_hash(hashes) -> str:
    """sha256 of sorted unique per-item hashes — merge primitive.

    Lets a partial re-run widen a row's ``content_hash`` to the union of
    everything analyzed for the same (source, date) without re-hashing items.
    """
    return hashlib.sha256("\n".join(sorted(set(hashes))).encode("utf-8")).hexdigest()


def analysed_hashes(analytics) -> set[str]:
    """Every item hash these saved analyses actually stored.

    The read side of the "delete the raw copy only after a successful save"
    rule: retirement keys on this set, so a partial analysis (one day of three
    failed, or a day the LLM rejected) leaves exactly the unanalysed rows
    staged instead of declaring the whole batch consumed. Lives here because
    the meaning of ``summary_data["content_hashes"]`` belongs with the rest of
    the hash vocabulary, not with either caller.
    """
    hashes: set[str] = set()
    for row in analytics or []:
        hashes.update((getattr(row, "summary_data", None) or {}).get("content_hashes") or [])
    return hashes


def batch_hash(items: list[dict]) -> str:
    """sha256 of the sorted per-item hashes — order-independent."""
    return hashes_hash(item_hash(i) for i in items)


async def filter_analyzed(
    content: list[dict],
    source_id: int,
    lookback_days: int = LOOKBACK_DAYS,
) -> tuple[list[dict], Optional[object]]:
    """Split content into not-yet-analyzed items.

    Returns ``(new_items, existing_row)``:
    - ``new_items`` — items with no known hash (analyze exactly these);
    - ``existing_row`` — the analysis that already covers every item, to be
      returned as-is when ``new_items`` is empty (``None`` when some items
      are new).

    Fail-open: any unexpected error returns the full batch so a dedup bug
    degrades to "pay twice", never to "analysis silently skipped".
    """
    from app.models import AIAnalytics

    if not content:
        return [], None

    try:
        rows = (
            await AIAnalytics.objects.filter(
                source_id=source_id,
                analysis_date__gte=date.today() - timedelta(days=lookback_days),
            )
            .order_by(AIAnalytics.analysis_date.desc())
            .limit(MAX_ANALYSES)
        )
    except Exception as e:
        logger.warning(f"Dedup lookup failed for source {source_id}, analyzing full batch: {e}")
        return content, None

    try:
        return _split(content, rows, source_id)
    except Exception as e:
        logger.warning(f"Dedup matching failed for source {source_id}, analyzing full batch: {e}")
        return content, None


# ---------------------------------------------------------------------------
# "Have we ever fetched this?" — a separate question from "have we analysed it?"
# ---------------------------------------------------------------------------
#
# The two must not share one index. An item that was fetched and staged for a
# later analysis has never been analysed (the analyser must still process it),
# yet it is certainly not new. Merging the ledgers would make the analyser skip
# staged items and silently drop them; keeping them apart means an item can be
# "seen but unanalysed", which is exactly the state the collected_items table
# exists to represent.

# How far back collect runs are consulted for the "seen" answer.
SEEN_LOOKBACK_DAYS = 30
SEEN_MAX_JOBS = 100


async def seen_hashes(source_id: int) -> set[str]:
    """Every item hash this source has ever handed us.

    Three sources, deliberately in one place so the "новых" counter can never
    disagree with what the staging table holds:
      1. `ai_analytics.summary_data["content_hashes"]` — analysed;
      2. `collected_items.content_hash` — fetched and waiting for analysis;
      3. `jobs.result["per_source"][].content_hashes` — fetched inline (analysed
         immediately, so never staged) — this is what keeps a run that analyses
         inline from re-reporting the same wall as new forever.
    """
    hashes: set[str] = set()

    from app.models import AIAnalytics, CollectedItem, Job

    try:
        rows = (
            await AIAnalytics.objects.filter(
                source_id=source_id,
                analysis_date__gte=date.today() - timedelta(days=SEEN_LOOKBACK_DAYS),
            )
            .order_by(AIAnalytics.analysis_date.desc())
            .limit(MAX_ANALYSES)
        )
        for row in rows:
            hashes.update((row.summary_data or {}).get("content_hashes") or [])
            if row.content_hash:
                hashes.add(row.content_hash)
    except Exception as e:
        logger.warning(f"Seen lookup (analytics) failed for source {source_id}: {e}")

    try:
        hashes.update(await CollectedItem.objects.hashes_for_source(source_id))
    except Exception as e:
        logger.warning(f"Seen lookup (staged) failed for source {source_id}: {e}")

    try:
        jobs = (
            await Job.objects.filter(job_type="collect")
            .order_by(Job.created_at.desc())
            .limit(SEEN_MAX_JOBS)
        )
        for job in jobs:
            result = job.result if isinstance(job.result, dict) else {}
            for entry in result.get("per_source") or []:
                if entry.get("source_id") == source_id:
                    hashes.update(entry.get("content_hashes") or [])
    except Exception as e:
        logger.warning(f"Seen lookup (jobs) failed for source {source_id}: {e}")

    return hashes


async def count_new_items(content: list[dict], source_id: int) -> int:
    """How many of `content` this source has never handed us before.

    Reports what a collection actually found, which is not what the analyser
    will pay for: an item staged by an earlier run is seen (not new) even though
    it still waits for analysis.

    Fails open to ``len(content)``: over-reporting only makes a run look busier,
    while under-reporting would claim there was nothing to do when there was.
    """
    if not content:
        return 0
    try:
        known = await seen_hashes(source_id)
        return sum(1 for item in content if item_hash(item) not in known)
    except Exception as e:  # noqa: BLE001 — a counting problem must not fail the collection
        logger.warning(f"Could not count new items for source {source_id}: {e}")
        return len(content)


def _split(content: list[dict], rows, source_id: int) -> tuple[list[dict], Optional[object]]:
    """Core matching: split content into not-yet-analyzed items."""
    batch_map: dict[str, object] = {}
    item_map: dict[str, object] = {}
    for row in rows:
        if row.content_hash:
            batch_map.setdefault(row.content_hash, row)
        for h in (row.summary_data or {}).get("content_hashes") or []:
            item_map.setdefault(h, row)

    batch = batch_hash(content)
    if batch in batch_map:
        logger.info(
            f"Dedup: identical batch ({len(content)} items) already analyzed "
            f"for source {source_id} (analytics {getattr(batch_map[batch], 'id', '?')})"
        )
        return [], batch_map[batch]

    new_items: list[dict] = []
    matched = None
    for item in content:
        h = item_hash(item)
        known = item_map.get(h)
        if known is not None:
            matched = matched or known
            continue
        new_items.append(item)

    if not new_items and matched is not None:
        logger.info(
            f"Dedup: all {len(content)} items already analyzed for source {source_id} "
            f"(analytics {getattr(matched, 'id', '?')})"
        )
        return [], matched

    if matched is not None or len(new_items) != len(content):
        logger.info(
            f"Dedup: {len(content) - len(new_items)} of {len(content)} items "
            f"already analyzed for source {source_id}"
        )
    return new_items, None
