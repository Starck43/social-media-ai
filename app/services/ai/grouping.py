"""Orthogonal grouping helpers for AI analytics rows.

``group_analytics()`` replaces the old ``_group_by_*`` methods on
``ReportAggregator``. One row can appear in multiple groupings simultaneously,
so the function is a pure helper over ``list[AIAnalytics]`` with optional
source-name resolution via the ORM.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from app.models import AIAnalytics, Source
from app.services.ai.chain_resolver import human_chain_label
from app.types.enums.bot_types import GroupingAxis


# ── low-level extractors ──────────────────────────────────────────────────────


def _text_analysis(summary_data: dict) -> dict:
    """Parsed text-analysis payload from summary_data."""
    return ((summary_data or {}).get("multi_llm_analysis") or {}).get("text_analysis") or {}


def _digest_value(summary_data: dict, keys: tuple[str, ...]):
    """First non-empty value for keys across known JSONB shapes."""
    if not summary_data:
        return None
    nests = [
        (summary_data.get("multi_llm_analysis") or {}).get("text_analysis"),
        summary_data.get("unified_summary"),
        summary_data.get("ai_analysis"),
        summary_data,
    ]
    for nest in nests:
        if not isinstance(nest, dict):
            continue
        for key in keys:
            value = nest.get(key)
            if value is not None and value != "" and value != []:
                return value
    return None


def _extract_topics(summary_data: dict) -> list[str]:
    """Extract topics/keywords from summary_data JSON."""
    if not summary_data:
        return []
    text = _text_analysis(summary_data)
    topics = text.get("main_topics") or text.get("topics") or text.get("key_topics") or []
    if not isinstance(topics, list):
        return []
    out: list[str] = []
    for t in topics:
        if isinstance(t, str):
            out.append(t)
        elif isinstance(t, dict):
            name = t.get("topic") or t.get("name") or t.get("key")
            if name:
                out.append(str(name))
    return out


def _extract_entities(summary_data: dict) -> list[dict]:
    """Extract mentioned entities from summary_data JSON.

    Contract: [{"name": str, "type": "person|brand|org", "context": str}]
    under multi_llm_analysis.text_analysis.entities.
    """
    if not summary_data:
        return []
    text_analysis = _text_analysis(summary_data)
    entities = text_analysis.get("entities") or []
    result: list[dict] = []
    for e in entities:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name") or "").strip()
        if not name:
            continue
        result.append(
            {
                "name": name[:255],
                "type": str(e.get("type") or "org").strip()[:50] or "org",
                "context": str(e.get("context") or "").strip()[:300],
            }
        )
    return result


def _extract_sentiment(summary_data: dict) -> dict | None:
    """Extract sentiment data from summary_data JSON."""
    if not summary_data:
        return None

    multi_llm = summary_data.get("multi_llm_analysis", {})
    text_analysis = multi_llm.get("text_analysis", {})

    if "sentiment_score" in text_analysis:
        score = text_analysis["sentiment_score"]
        if score > 0.6:
            label = "positive"
        elif score < 0.4:
            label = "negative"
        else:
            label = "neutral"
        return {"label": label, "score": score}

    ai_analysis = summary_data.get("ai_analysis", {})
    sentiment = ai_analysis.get("sentiment_analysis", {})
    if sentiment and "overall_sentiment" in sentiment:
        score = sentiment["overall_sentiment"]
        if isinstance(score, (int, float)):
            if score > 0.6:
                label = "positive"
            elif score < 0.4:
                label = "negative"
            else:
                label = "neutral"
            return {"label": label, "score": float(score)}
    return None


def _extract_intent(summary_data: dict) -> str | None:
    """Extract intent type from summary_data JSON."""
    if not summary_data:
        return None
    text = _text_analysis(summary_data)
    flat = text.get("intent_type")
    if flat:
        return str(flat).lower()
    primary = _digest_value(summary_data, ("primary_intent", "user_intent"))
    if primary:
        return str(primary).lower()
    return None


def _extract_media_types(row: AIAnalytics) -> list[str]:
    """Normalize media_types from an AIAnalytics row."""
    media = row.media_types or []
    out: list[str] = []
    for m in media:
        if isinstance(m, dict):
            m = m.get("type") or m.get("name") or m.get("media_type") or ""
        if isinstance(m, str) and m.strip():
            out.append(m.strip())
    return out


def _chain_author(chain_id: str | None) -> str | None:
    """Extract the tracked user from a chain id like `src_5_user_123`."""
    if not chain_id:
        return None
    marker = "_user_"
    if marker in chain_id:
        return chain_id.split(marker, 1)[1]
    return None


def _content_stats(summary_data: dict) -> dict[str, int]:
    """Extract content_statistics from summary_data JSON."""
    if not summary_data:
        return {}
    text_analysis = ((summary_data or {}).get("multi_llm_analysis") or {}).get("text_analysis") or {}
    cs = text_analysis.get("content_statistics")
    if isinstance(cs, dict):
        return cs
    return summary_data.get("content_statistics") or {}


# ── source name cache ─────────────────────────────────────────────────────────


async def _source_name_map(source_ids: list[int]) -> dict[int, str]:
    """Resolve source ids to display names."""
    rows = await Source.objects.filter(id__in=source_ids).all()
    return {s.id: s.name or f"Источник #{s.id}" for s in rows}


# ── main grouping function ────────────────────────────────────────────────────


async def group_analytics(
    rows: list[AIAnalytics],
    axis: GroupingAxis | str,
    time_breakdown: bool = False,
    entity_type: str | None = None,
) -> dict[str, Any]:
    """Group analytics rows by the chosen axis.

    Args:
        rows: List of AIAnalytics rows to group.
        axis: Grouping axis (GroupingAxis enum or its string value).
        time_breakdown: If True, include per-date sub-entries within each group.
        entity_type: Optional filter for ENTITIES axis. One of "brand", "person",
            "org". When None, all entity types are included. Ignored for other axes.

    Returns a dict suitable for digest text rendering and API JSON:
    {
        "axis": "entities",
        "time_breakdown": false,
        "entity_type": "person",  # present only when filtering entities
        "groups": [
            {
                "key": "Coca-Cola",
                "count": 12,
                "avg_sentiment": 0.7,
                "entries": [...]   # only if time_breakdown=True
            }
        ]
    }
    """
    if isinstance(axis, str):
        axis = GroupingAxis(axis)
    if not rows:
        result: dict[str, Any] = {"axis": axis.value, "time_breakdown": time_breakdown, "groups": []}
        if axis == GroupingAxis.ENTITIES and entity_type:
            result["entity_type"] = entity_type
        return result

    # Validate entity_type filter
    if entity_type is not None and axis != GroupingAxis.ENTITIES:
        raise ValueError(f"entity_type filter is only valid for ENTITIES axis, got axis={axis.value}")
    if entity_type is not None:
        valid_types = {"brand", "person", "org"}
        if entity_type not in valid_types:
            raise ValueError(f"entity_type must be one of {valid_types}, got '{entity_type}'")

    groups: dict[str, dict[str, Any]] = {}
    source_ids_needed: set[int] = set()

    for row in rows:
        key: str | None = None

        if axis == GroupingAxis.THEMES:
            topics = _extract_topics(row.summary_data)
            if topics:
                key = topics[0]
            else:
                key = "(без темы)"

        elif axis == GroupingAxis.SOURCES:
            key = str(row.source_id)
            source_ids_needed.add(row.source_id)

        elif axis == GroupingAxis.ENTITIES:
            for ent in _extract_entities(row.summary_data):
                if entity_type and ent["type"] != entity_type:
                    continue
                key = ent["name"]
                bucket = groups.setdefault(
                    key,
                    {"key": key, "count": 0, "scores": [], "entries": defaultdict(list)},
                )
                bucket["count"] += 1
                sent = _extract_sentiment(row.summary_data)
                if sent and sent.get("score") is not None:
                    bucket["scores"].append(float(sent["score"]))
                if time_breakdown:
                    day = row.analysis_date.isoformat() if row.analysis_date else "unknown"
                    bucket["entries"][day].append(row)
            continue

        elif axis == GroupingAxis.SENTIMENT:
            sent = _extract_sentiment(row.summary_data)
            key = sent["label"] if sent else "unknown"

        elif axis == GroupingAxis.CONTENT_TYPE:
            media_types = _extract_media_types(row)
            if media_types:
                for mt in media_types:
                    key = mt
                    bucket = groups.setdefault(
                        key,
                        {"key": key, "count": 0, "scores": [], "entries": defaultdict(list)},
                    )
                    bucket["count"] += 1
                    sent = _extract_sentiment(row.summary_data)
                    if sent and sent.get("score") is not None:
                        bucket["scores"].append(float(sent["score"]))
                    if time_breakdown:
                        day = row.analysis_date.isoformat() if row.analysis_date else "unknown"
                        bucket["entries"][day].append(row)
            else:
                key = "unknown"
                bucket = groups.setdefault(
                    key,
                    {"key": key, "count": 0, "scores": [], "entries": defaultdict(list)},
                )
                bucket["count"] += 1
                sent = _extract_sentiment(row.summary_data)
                if sent and sent.get("score") is not None:
                    bucket["scores"].append(float(sent["score"]))
                if time_breakdown:
                    day = row.analysis_date.isoformat() if row.analysis_date else "unknown"
                    bucket["entries"][day].append(row)
            continue

        elif axis == GroupingAxis.INTENT:
            intent = _extract_intent(row.summary_data)
            key = intent or "unknown"

        elif axis == GroupingAxis.DAYS:
            key = row.analysis_date.isoformat() if row.analysis_date else "unknown"

        elif axis == GroupingAxis.MONITORED_USERS:
            author = _chain_author(row.topic_chain_id)
            if author is None:
                continue
            key = author

        elif axis == GroupingAxis.CHAINS:
            if not row.topic_chain_id:
                continue
            key = row.topic_chain_id

        if key is None:
            continue

        bucket = groups.setdefault(
            key,
            {"key": key, "count": 0, "scores": [], "entries": defaultdict(list)},
        )
        bucket["count"] += 1
        sent = _extract_sentiment(row.summary_data)
        if sent and sent.get("score") is not None:
            bucket["scores"].append(float(sent["score"]))
        if time_breakdown:
            day = row.analysis_date.isoformat() if row.analysis_date else "unknown"
            bucket["entries"][day].append(row)

    # Resolve source names
    if axis == GroupingAxis.SOURCES and source_ids_needed:
        names = await _source_name_map(sorted(source_ids_needed))
        for bucket in groups.values():
            try:
                bucket["key"] = names.get(int(bucket["key"]), bucket["key"])
            except (ValueError, TypeError):
                pass

    # Build result list
    result_groups: list[dict[str, Any]] = []
    for key, bucket in groups.items():
        scores = bucket["scores"]
        avg_sent = round(sum(scores) / len(scores), 2) if scores else None
        group: dict[str, Any] = {
            "key": bucket["key"],
            "count": bucket["count"],
            "avg_sentiment": avg_sent,
        }

        if axis == GroupingAxis.DAYS:
            day_rows = [r for r in rows if (r.analysis_date.isoformat() if r.analysis_date else "unknown") == key]
            cs = _content_stats(day_rows[0].summary_data) if day_rows else {}
            group.update(
                {
                    "posts": sum(
                        int((_content_stats(r.summary_data).get("total_posts") or 0)) for r in day_rows
                    ),
                    "messages": sum(
                        int((_content_stats(r.summary_data).get("messages_count") or 0)) for r in day_rows
                    ),
                    "users": sum(
                        int((_content_stats(r.summary_data).get("active_users") or 0)) for r in day_rows
                    ),
                }
            )

        elif axis == GroupingAxis.MONITORED_USERS:
            user_rows = [r for r in rows if _chain_author(r.topic_chain_id) == key]
            group.update(
                {
                    "posts": sum(
                        int((_content_stats(r.summary_data).get("total_posts") or 0)) for r in user_rows
                    ),
                    "messages": sum(
                        int((_content_stats(r.summary_data).get("messages_count") or 0)) for r in user_rows
                    ),
                }
            )

        elif axis == GroupingAxis.CHAINS:
            chain_rows = [r for r in rows if r.topic_chain_id == key]
            label = next(
                (r.chain_label for r in chain_rows if getattr(r, "chain_label", None)),
                next((human_chain_label(r.summary_data) for r in chain_rows if human_chain_label(r.summary_data)), key),
            )
            dates = [r.analysis_date for r in chain_rows if r.analysis_date]
            span = ""
            if dates:
                span = f"{min(dates).isoformat()} — {max(dates).isoformat()}"
            group.update({"label": label, "date_span": span})

        if time_breakdown:
            entries = []
            for day in sorted(bucket["entries"]):
                day_rows = bucket["entries"][day]
                day_scores = []
                for r in day_rows:
                    s = _extract_sentiment(r.summary_data)
                    if s and s.get("score") is not None:
                        day_scores.append(float(s["score"]))
                entries.append(
                    {
                        "date": day,
                        "count": len(day_rows),
                        "avg_sentiment": round(sum(day_scores) / len(day_scores), 2) if day_scores else None,
                    }
                )
            group["entries"] = entries
        result_groups.append(group)

    result_groups.sort(key=lambda g: -g["count"])

    result: dict[str, Any] = {"axis": axis.value, "time_breakdown": time_breakdown, "groups": result_groups}
    if axis == GroupingAxis.ENTITIES and entity_type:
        result["entity_type"] = entity_type
    return result
