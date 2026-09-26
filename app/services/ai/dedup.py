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
