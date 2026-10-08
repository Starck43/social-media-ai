"""Pure stored-data rendering: missing metrics never masquerade as measured zero."""

from __future__ import annotations

import math
import re
from typing import Any
from urllib.parse import urlsplit


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def metric_number(value: Any) -> float | None:
    """Finite nonnegative counts/scores only; bool is not a measured number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def safe_original_url(value: Any) -> str | None:
    """Only an explicitly saved HTTP(S) original, never a synthesized post URL."""
    if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(c) < 32 for c in value) or "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
            return None
        parsed.port
    except ValueError:
        return None
    return value


def extract_text_analysis(summary_data: dict[str, Any]) -> dict[str, Any]:
    multi = _as_dict(summary_data.get("multi_llm_analysis"))
    text = _as_dict(multi.get("text_analysis"))
    return {**text, **_as_dict(text.get("parsed"))}


def extract_content_statistics(summary_data: dict[str, Any]) -> dict[str, Any]:
    """Conservative display view; stored legacy numeric aggregates stay unchanged.

    Coverage is all-or-nothing for a total. Partial counts are not full totals.
    Historical zero engagement counts without provenance are unverified (None).
    """
    stats = _as_dict(summary_data.get("content_statistics"))
    coverage = _as_dict(stats.get("metric_coverage"))
    keys = (
        "total_posts",
        "total_reactions",
        "total_comments",
        "total_views",
        "active_users",
        "messages_count",
        "engagement_rate",
    )
    out = {}
    for key in keys:
        value = metric_number(stats.get(key))
        info = _as_dict(coverage.get(key))
        if info:
            if (
                type(info.get("known")) is not int
                or type(info.get("total")) is not int
                or info["total"] <= 0
                or info["known"] != info["total"]
            ):
                value = None
        elif value == 0:
            value = None
        out[key] = value
    for name, total_key in (
        ("avg_reactions_per_post", "total_reactions"),
        ("avg_comments_per_post", "total_comments"),
        ("avg_views_per_post", "total_views"),
    ):
        posts, total = out["total_posts"], out[total_key]
        out[name] = total / posts if posts is not None and posts > 0 and total is not None else None
    if _as_dict(summary_data.get("period_rollup")):
        out["active_users"] = None  # Summed daily authors are not unique period authors.
    return out


def _label_for_score(score: float) -> str:
    return "Позитивный" if score > 0.6 else "Негативный" if score < 0.4 else "Нейтральный"


def sentiment_summary(summary_data: dict[str, Any]) -> dict[str, Any]:
    text = extract_text_analysis(summary_data)
    unified = _as_dict(summary_data.get("unified_summary"))
    unified = {**unified, **_as_dict(unified.get("parsed"))}
    score = None
    for container in (text, unified, summary_data):
        candidate = metric_number(container.get("sentiment_score"))
        if candidate is not None and candidate <= 1:
            score = candidate
            break
    return {"score": score, "label": _label_for_score(score) if score is not None else "—"}


def summary_display_heading(summary: Any, limit: int = 120) -> str | None:
    """A short stored-summary excerpt, not an invented title or execution status."""
    if not isinstance(summary, str):
        return None
    value = " ".join(summary.split())
    if not value:
        return None
    # Conservative sentence boundaries: keep decimals and common abbreviations
    # (e.g. ул. Ленина, г. Киров, т. д.) intact rather than cutting at every dot.
    abbreviations = {
        "г",
        "ул",
        "д",
        "им",
        "др",
        "т",
        "п",
        "рис",
        "стр",
        "см",
        "руб",
        "млн",
        "млрд",
        "mr",
        "mrs",
        "ms",
        "dr",
        "vs",
        "т.д",
        "т.п",
        "т.е",
        "e.g",
        "i.e",
    }
    for match in re.finditer(r"[.!?…]+(?=\s|$)", value):
        prefix = value[: match.start()]
        token = re.search(r"([^\s]+)$", prefix)
        word = token.group(1) if token else ""
        if match.group() == "." and (
            word.casefold().rstrip(".") in abbreviations or (len(word) == 1 and word.isupper())
        ):
            continue
        value = value[: match.end()]
        break
    if len(value) <= limit:
        return value
    shortened = value[: limit - 1].rstrip()
    boundary = shortened.rfind(" ")
    if boundary >= limit // 2:
        shortened = shortened[:boundary]
    return shortened.rstrip(" .,;:!?…") + "…"


def render_analysis(summary_data: dict[str, Any], *, source_name: str | None = None) -> dict[str, Any]:
    data = _as_dict(summary_data)
    text = extract_text_analysis(data)
    unified = _as_dict(data.get("unified_summary"))
    unified = {**unified, **_as_dict(unified.get("parsed"))}
    legacy = _as_dict(data.get("ai_analysis"))
    legacy = {**legacy, **_as_dict(legacy.get("parsed"))}
    containers = (data, text, unified, legacy)

    def first(*keys):
        for container in containers:
            for key in keys:
                value = container.get(key)
                if value is not None and value != "" and value != []:
                    return value
        return None

    raw_topics = _as_list(first("main_topics", "topics", "key_topics"))
    topics = []
    for topic in raw_topics:
        value = (
            topic
            if isinstance(topic, str)
            else (_as_dict(topic).get("topic") or _as_dict(topic).get("name") or _as_dict(topic).get("key"))
        )
        if isinstance(value, str) and value.strip() and value.strip() not in topics:
            topics.append(value.strip())
    title = next(
        (
            c["analysis_title"].strip()
            for c in containers
            if isinstance(c.get("analysis_title"), str) and c["analysis_title"].strip()
        ),
        None,
    )
    summary = next(
        (
            c[key].strip()
            for c in containers
            for key in ("analysis_summary", "summary")
            if isinstance(c.get(key), str) and c[key].strip()
        ),
        None,
    )
    # One stored-data cascade shared by every web analysis entry point.
    display_title = title or summary_display_heading(summary) or (topics[0] if topics else None)
    title = title or (topics[0] if topics else None)
    stats = _as_dict(data.get("content_statistics"))
    source_meta = _as_dict(data.get("source_metadata"))
    name = source_name or source_meta.get("source_name")
    display_title = display_title or (
        f"Материалы источника «{name.strip()}»" if isinstance(name, str) and name.strip() else "Материалы источника"
    )
    originals = []
    candidates = _as_list(stats.get("original_links")) + _as_list(data.get("original_links"))
    for key in ("original_url", "post_url", "permalink"):
        candidates += [data.get(key), source_meta.get(key)]
    for candidate in candidates:
        url = safe_original_url(candidate.get("url") if isinstance(candidate, dict) else candidate)
        if url and url not in originals:
            originals.append(url)
    window = _as_dict(stats.get("content_date_range"))
    date_range = _as_dict(stats.get("date_range"))
    rollup = _as_dict(data.get("period_rollup"))
    return {
        "analysis_title": title,
        "display_title": display_title,
        "analysis_summary": summary,
        "main_topics": topics,
        "overall_mood": first("overall_mood"),
        "highlights": _as_list(first("highlights")),
        "highlights_available": any(isinstance(c.get("highlights"), list) for c in containers),
        "sentiment": sentiment_summary(data),
        "toxicity_score": first("toxicity_score"),
        "toxicity_category": first("toxicity_category"),
        "content_statistics": extract_content_statistics(data),
        "metric_coverage": _as_dict(stats.get("metric_coverage")),
        "metrics_legacy": not bool(stats.get("metric_coverage")),
        "source_metadata": source_meta,
        "scenario_metadata": _as_dict(data.get("scenario_metadata")),
        "analysis_metadata": _as_dict(data.get("analysis_metadata")),
        "unified_summary": unified,
        "original_links": originals[:50],
        "content_window_start": window.get("earliest") or rollup.get("start") or date_range.get("first"),
        "content_window_end": window.get("latest") or rollup.get("end") or date_range.get("last"),
    }
