"""Shared, framework-agnostic helpers for rendering one `AIAnalytics` row.

Both the web UI (`/app/analytics/{id}`, chain retrospectives) and the sqladmin
detail template consume the same JSON stored in `AIAnalytics.summary_data`, but
each used to reach into the raw dict with its own fragile `get()` chains that
differed subtly and silently disagreed. This module is the single place that
turns `summary_data` into a stable, template-friendly structure, so the two
surfaces render identically without duplicating the extraction.

Nothing here touches the database or the ORM — it is pure dict reshaping, so it
stays trivially unit-testable.
"""

from __future__ import annotations

from typing import Any


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def extract_text_analysis(summary_data: dict[str, Any]) -> dict[str, Any]:
    """The `text_analysis` block of `multi_llm_analysis`, as a flat dict."""
    multi = _as_dict(summary_data.get("multi_llm_analysis"))
    return _as_dict(multi.get("text_analysis"))


def extract_content_statistics(summary_data: dict[str, Any]) -> dict[str, Any]:
    """The `content_statistics` block, defaulting every metric to 0."""
    stats = _as_dict(summary_data.get("content_statistics"))
    return {
        "total_posts": stats.get("total_posts", 0) or 0,
        "total_reactions": stats.get("total_reactions", 0) or 0,
        "total_comments": stats.get("total_comments", 0) or 0,
        "total_views": stats.get("total_views", 0) or 0,
        "active_users": stats.get("active_users", 0) or 0,
        "messages_count": stats.get("messages_count", 0) or 0,
        "avg_reactions_per_post": stats.get("avg_reactions_per_post", 0) or 0,
        "avg_comments_per_post": stats.get("avg_comments_per_post", 0) or 0,
        "engagement_rate": stats.get("engagement_rate", 0) or 0,
    }


def sentiment_summary(summary_data: dict[str, Any]) -> dict[str, Any]:
    """The sentiment expressed by the text analysis, normalized.

    Returns ``{"score": float|None, "label": str}``. A None score means the
    analysis carried no usable sentiment value.
    """
    text = extract_text_analysis(summary_data)
    score = text.get("sentiment_score")
    if score is None:
        return {"score": None, "label": "—"}
    try:
        score = float(score)
    except (TypeError, ValueError):
        return {"score": None, "label": "—"}
    label = text.get("sentiment_label") or _label_for_score(score)
    return {"score": score, "label": label}


def _label_for_score(score: float) -> str:
    if score > 0.6:
        return "Позитивный"
    if score < 0.4:
        return "Негативный"
    return "Нейтральный"


def render_analysis(summary_data: dict[str, Any]) -> dict[str, Any]:
    """Flatten one row's `summary_data` into a display-ready structure.

    All blocks the templates show are extracted here with safe defaults, so a
    template never has to know where a value lives in the raw JSON.
    """
    summary_data = _as_dict(summary_data)
    text = extract_text_analysis(summary_data)
    unified = _as_dict(summary_data.get("unified_summary"))
    source_meta = _as_dict(summary_data.get("source_metadata"))
    scenario_meta = _as_dict(summary_data.get("scenario_metadata"))
    analysis_meta = _as_dict(summary_data.get("analysis_metadata"))
    sentiment = sentiment_summary(summary_data)

    # Title fallback chain: explicit analysis_title → top main_topic → chain_label.
    # The LLM is asked for `analysis_title` but doesn't always emit one, so the
    # first main topic (which the chain resolver already promoted to chain_label)
    # is the next-best human-readable title.
    analysis_title = summary_data.get("analysis_title")
    if not analysis_title:
        main_topics = _as_list(text.get("main_topics")) or _as_list(unified.get("main_topics"))
        if main_topics:
            analysis_title = str(main_topics[0])

    return {
        "analysis_title": analysis_title,
        "analysis_summary": summary_data.get("analysis_summary"),
        "main_topics": _as_list(text.get("main_topics")) or _as_list(unified.get("main_topics")),
        "overall_mood": text.get("overall_mood"),
        "highlights": _as_list(text.get("highlights")),
        "sentiment": sentiment,
        "toxicity_score": text.get("toxicity_score"),
        "toxicity_category": text.get("toxicity_category"),
        "content_statistics": extract_content_statistics(summary_data),
        "source_metadata": source_meta,
        "scenario_metadata": scenario_meta,
        "analysis_metadata": analysis_meta,
        "unified_summary": unified,
    }
