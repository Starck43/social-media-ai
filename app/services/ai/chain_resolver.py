"""Chain resolver: finds or creates topic chains per Phase 1 spec.

Resolution is deterministic — normalization + lookup, no LLM call.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.models import AIAnalytics

_RUSSIAN_SUFFIXES = [
    "ющими", "ющими",
    "ывани", "овани", "еван", "иван", "ани", "ени",
    "ывать", "ивать", "овать", "евать",
    "ать", "ять", "ить", "еть", "уть", "ыть",
    "ала", "ила", "ела", "ула", "али", "или", "ели", "ули",
    "ает", "яет", "ет", "ают", "яют", "ют",
    "аешь", "яешь", "иешь", "оешь", "уешь", "еешь",
    "аете", "яете", "иете", "оете", "уете", "еете",
    "аем", "яем", "ием", "оем", "уем", "еем",
    "ите", "ьте",
    "ский", "ческий", "ический",
    "тель", "чик", "щик", "ник",
    "енный", "овый", "евой", "авой",
    "ный", "ний", "ной", "ная", "ное", "ные", "ние",
    "ость", "ение", "ание", "ство",
    "ого", "его", "ому", "ему",
    "ыми", "ими",
    "ом", "ем", "ов", "ев", "ей",
    "ам", "ям", "ах", "ях",
    "ой", "ею", "ую", "юю",
    "ы", "и", "а", "я", "ь", "ъ", "у", "ю", "е", "о",
]


def _stem(word: str) -> str:
    for suffix in _RUSSIAN_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 2:
            return word[:-len(suffix)]
    return word


def _normalize(text: str) -> str:
    """Lowercase, ё→е, strip punctuation, collapse spaces, stem, trim to 255."""
    if not text:
        return ""
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]", "", text)
    text = " ".join(text.split())
    text = " ".join(_stem(p) for p in text.split())
    return text[:255]


def _token_set_ratio(a: str, b: str) -> float:
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a or not tokens_b:
        return 0.0
    inter = tokens_a & tokens_b
    if not inter:
        return 0.0
    a_common = " ".join(sorted(inter))
    b_common = " ".join(sorted(inter))
    a_full = " ".join(sorted(tokens_a))
    b_full = " ".join(sorted(tokens_b))

    def ratio(x: str, y: str) -> float:
        if not x and not y:
            return 1.0
        if not x or not y:
            return 0.0
        return SequenceMatcher(None, x, y).ratio()

    return max(
        ratio(a_common, b_common),
        ratio(a_common + " " + a_full, b_common + " " + b_full),
    )


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
    2. Normalize topic_hint (lowercase, ё→е, punctuation strip, stemming)
    3. Look up last ``lookback_days`` ai_analytics rows for this source
       where topic_chain_id IS NOT NULL
    4. Compare normalized ``topic_hint`` against stored hints:
       - exact normalized match → reuse chain
       - token_set_ratio >= 0.85 → reuse chain
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
        existing_hint = (
            summary.get("multi_llm_analysis", {})
            .get("text_analysis", {})
            .get("topic_hint")
        ) or summary.get("topic_hint") or ""
        if not existing_hint:
            continue
        existing_norm = _normalize(existing_hint)
        if not existing_norm:
            continue
        if existing_norm == normalized_hint:
            return row.topic_chain_id, row.chain_label or human_chain_label(summary) or row.topic_chain_id
        if _token_set_ratio(normalized_hint, existing_norm) >= 0.85:
            logger.info(
                "Chain linked by similarity: %s ~ %s (token_set_ratio=%.2f)",
                topic_hint, existing_hint, _token_set_ratio(normalized_hint, existing_norm),
            )
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

    counters = []
    for eid in existing:
        if eid == base:
            continue
        suffix = eid[len(base) + 1:] if eid.startswith(base + "-") else ""
        if suffix.isdigit():
            counters.append(int(suffix))

    next_counter = max(counters, default=0) + 1
    return f"{base}-{next_counter}"
