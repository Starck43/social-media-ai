"""Chain resolver: finds or creates topic chains per Phase 1 spec.

Resolution is deterministic — normalization + lookup, no LLM call.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import AIAnalytics


def _normalize(text: str) -> str:
    """Lowercase, strip, drop punctuation, trim to 20 chars."""
    cleaned = re.sub(r"[^\w\s]", "", text.lower().strip())
    return " ".join(cleaned.split())[:20]


def human_chain_label(summary_data: dict | None) -> str | None:
    """Human-readable chain label from a stored `summary_data` document.

    Handles both the current contract (topics under
    `multi_llm_analysis.text_analysis.parsed`) and the legacy layout (topics
    directly under `multi_llm_analysis.text_analysis`, written before the
    JSON schema gained `parsed`). Prefers the top topic, then any analysis
    title, then returns None so callers fall back to the chain id.
    """
    if not summary_data:
        return None
    text_analysis = (summary_data.get("multi_llm_analysis") or {}).get("text_analysis") or {}
    parsed = text_analysis.get("parsed") or {}
    topics = parsed.get("main_topics") or text_analysis.get("main_topics") or []
    if topics:
        return str(topics[0]).strip()[:255]
    title = (
        parsed.get("analysis_title")
        or text_analysis.get("analysis_title")
        or summary_data.get("analysis_title")
    )
    if title:
        return str(title).strip()[:255]
    return None


async def resolve_chain_async(
    tenant_id: int,
    source_id: int,
    topic_hint: str | None,
    lookback_days: int = 30,
) -> tuple[str, str]:
    """Find or create a topic chain for (source, topic_hint).

    Algorithm (Phase 1):
    1. If topic_hint is None → return (f"src_{source_id}_general", "Общая тема")
    2. Normalize topic_hint
    3. Look up last ``lookback_days`` ai_analytics rows for this source
       where topic_chain_id IS NOT NULL
    4. Compare normalized ``topic_hint`` (nested contract first, then top-level)
    5. If match found → return existing (topic_chain_id, chain_label)
    6. If no match → generate new chain_id from topic_hint slug (transliterated),
       return (chain_id, topic_hint)
    """
    if not topic_hint:
        return f"src_{source_id}_general", "Общая тема"

    normalized_hint = _normalize(topic_hint)
    if not normalized_hint:
        return f"src_{source_id}_general", "Общая тема"

    from app.core.database import async_session_maker
    from sqlalchemy import select, and_
    from app.models import AIAnalytics

    cutoff: date = date.today() - timedelta(days=lookback_days)

    async with async_session_maker() as s:
        stmt = select(AIAnalytics).where(
            and_(
                AIAnalytics.tenant_id == tenant_id,
                AIAnalytics.source_id == source_id,
                AIAnalytics.topic_chain_id.isnot(None),
                AIAnalytics.analysis_date >= cutoff,
            )
        ).order_by(AIAnalytics.analysis_date.desc())

        result = await s.execute(stmt)
        rows: list[AIAnalytics] = list(result.scalars().all())

    for row in rows:
        summary = row.summary_data or {}
        # Current format: multi_llm_analysis.text_analysis.topic_hint
        existing_hint = (
            summary.get("multi_llm_analysis", {})
            .get("text_analysis", {})
            .get("topic_hint")
        ) or summary.get("topic_hint") or ""
        if _normalize(existing_hint) == normalized_hint:
            return row.topic_chain_id, row.chain_label or human_chain_label(summary) or row.topic_chain_id

    new_id = await _generate_chain_slug(normalized_hint)
    return new_id, topic_hint[:255]


async def _generate_chain_slug(topic_hint: str) -> str:
    """Generate a human-readable chain slug from a topic hint.

    Uses transliteration so Cyrillic topics become URL-safe Latin slugs.
    Appends a counter on collision (e.g. "kiberbezopasnost", "kiberbezopasnost-1").
    """
    from app.core.database import async_session_maker
    from app.utils.translit import translit_slug
    from sqlalchemy import select
    from app.models import AIAnalytics

    base = translit_slug(topic_hint)
    if not base:
        return "obschaya-tema"

    async with async_session_maker() as s:
        stmt = select(AIAnalytics.topic_chain_id).where(
            AIAnalytics.topic_chain_id.like(f"{base}%")
        )
        result = await s.execute(stmt)
        existing = list(result.scalars().all())

    if base not in existing:
        return base

    # Collision: find the highest counter and increment
    counters = []
    for eid in existing:
        if eid == base:
            continue
        # Match base-N pattern
        suffix = eid[len(base) + 1:] if eid.startswith(base + "-") else ""
        if suffix.isdigit():
            counters.append(int(suffix))

    next_counter = max(counters, default=0) + 1
    return f"{base}-{next_counter}"
