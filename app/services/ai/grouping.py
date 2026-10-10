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
from app.services.ai.analysis_render import first_analysis_value, render_analysis
from app.services.ai.chain_resolver import human_chain_label
from app.services.ai.reporting import MEDIA_FILTERS, SENTIMENT_FILTERS, sentiment_bucket
from app.types.enums.bot_types import GroupingAxis

# ── low-level extractors ──────────────────────────────────────────────────────


def _text_analysis(summary_data: dict) -> dict:
    """Parsed text-analysis payload from summary_data."""
    text = ((summary_data or {}).get("multi_llm_analysis") or {}).get("text_analysis") or {}
    return {**text, **text.get("parsed", {})} if isinstance(text.get("parsed"), dict) else text


def _digest_value(summary_data: dict, keys: tuple[str, ...]):
    """First non-empty value for keys across known JSONB shapes."""
    if not summary_data:
        return None
    nests = [
        _text_analysis(summary_data),
        summary_data.get("unified_summary"),
        summary_data.get("ai_analysis"),
        summary_data,
    ]
    return first_analysis_value(nests, keys)


def _extract_topics(summary_data: dict) -> list[str]:
    """Extract topics/keywords from summary_data JSON."""
    if not summary_data:
        return []
    topics = _digest_value(summary_data, ("main_topics", "topics", "key_topics")) or []
    if not isinstance(topics, list):
        return []
    out: list[str] = []
    for t in topics:
        if isinstance(t, str) and t.strip():
            out.append(t.strip())
        elif isinstance(t, dict):
            name = t.get("topic") or t.get("name") or t.get("key")
            if name:
                out.append(str(name).strip())
    return out


def _extract_entities(summary_data: dict) -> list[dict]:
    """Extract mentioned entities from summary_data JSON.

    Contract: [{"name": str, "type": "person|brand|org", "context": str}]
    under multi_llm_analysis.text_analysis.entities.
    """
    if not summary_data:
        return []
    entities = _digest_value(summary_data, ("entities",)) or []
    if not isinstance(entities, list):
        return []
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
    """Read current flat/nested and legacy numeric scores with shared buckets."""
    if not isinstance(summary_data, dict):
        return None
    text = _text_analysis(summary_data)
    candidates = [text.get("sentiment_score"), summary_data.get("sentiment_score")]
    for container in (text, summary_data.get("ai_analysis") or {}, summary_data):
        sentiment = container.get("sentiment_analysis") or {}
        if not isinstance(sentiment, dict):
            continue
        candidates.append(sentiment.get("sentiment_score"))
        overall = sentiment.get("overall_sentiment")
        candidates.append(overall.get("score") if isinstance(overall, dict) else overall)
    for score in candidates:
        label = sentiment_bucket(score)
        if label:
            return {"label": label, "score": float(score)}
    return None


def _extract_intent(summary_data: dict) -> str | None:
    """Extract intent type from summary_data JSON."""
    if not summary_data:
        return None
    text = _text_analysis(summary_data)
    flat = _digest_value(summary_data, ("intent_type",))
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


def _format_period_key(key: str, group_by_period: str | None) -> str:
    """Format a period key for Russian locale display."""
    if group_by_period == "week":
        parts = key.split("-")
        if len(parts) == 2 and parts[1].startswith("W"):
            return f"Неделя {parts[1][1:]}"
        return key
    if group_by_period == "month":
        parts = key.split("-")
        if len(parts) == 2:
            year, month = int(parts[0]), int(parts[1])
            months = [
                "январь",
                "февраль",
                "март",
                "апрель",
                "май",
                "июнь",
                "июль",
                "август",
                "сентябрь",
                "октябрь",
                "ноябрь",
                "декабрь",
            ]
            return f"{months[month - 1]} {year}"
        return key
    # day: format ISO date for Russian locale
    parts = key.split("-")
    if len(parts) == 3:
        from datetime import date as dt_date
        from datetime import timedelta

        try:
            d = dt_date(int(parts[0]), int(parts[1]), int(parts[2]))
            today = dt_date.today()
            if d == today:
                return "сегодня"
            yesterday = today - timedelta(days=1)
            if d == yesterday:
                return "вчера"
            months = [
                "января",
                "февраля",
                "марта",
                "апреля",
                "мая",
                "июня",
                "июля",
                "августа",
                "сентября",
                "октября",
                "ноября",
                "декабря",
            ]
            if d.year == today.year:
                return f"{d.day} {months[d.month - 1]}"
            return f"{d.day} {months[d.month - 1]} {d.year}"
        except (ValueError, TypeError):
            pass
    return key


def _period_key(d: date, group_by_period: str | None) -> str:
    """Compute a display key for a date under the chosen period grouping."""
    if group_by_period == "week":
        iso = d.isocalendar()
        return f"{d.year}-W{iso.week:02d}"
    if group_by_period == "month":
        return d.strftime("%Y-%m")
    return d.isoformat()


def _serialize_row(row: AIAnalytics) -> dict[str, Any]:
    """Serialize an AIAnalytics row for template rendering in timeline views."""
    sent = _extract_sentiment(row.summary_data)
    display = render_analysis(row.summary_data)
    return {
        "id": row.id,
        "analysis_date": row.analysis_date.isoformat() if row.analysis_date else None,
        "source_id": row.source_id,
        "analysis_title": display["analysis_title"],
        "display_title": display["display_title"],
        "sentiment_score": sent.get("score") if sent else None,
        "sentiment_label": sent.get("label") if sent else None,
        "main_topics": _extract_topics(row.summary_data),
        "topic_chain_id": row.topic_chain_id,
        "chain_label": row.chain_label,
        "analysis_summary": display["analysis_summary"],
    }


# ── source name cache ─────────────────────────────────────────────────────────


async def _source_name_map(source_ids: list[int]) -> dict[int, str]:
    """Resolve source ids to display names."""
    rows = await Source.objects.filter(id__in=source_ids).all()
    return {s.id: s.name or f"Источник #{s.id}" for s in rows}


# ── main grouping function ────────────────────────────────────────────────────


def filter_analytics(
    rows: list[AIAnalytics], sentiment: str | None = None, media: str | None = None
) -> list[AIAnalytics]:
    """Apply cross-axis filters before counts, averages or chronological slices."""
    if sentiment is not None and sentiment not in SENTIMENT_FILTERS:
        raise ValueError(f"sentiment must be one of {', '.join(SENTIMENT_FILTERS)}")
    if media is not None and media not in MEDIA_FILTERS:
        raise ValueError(f"media must be one of {', '.join(MEDIA_FILTERS)}")
    filtered = []
    for row in rows:
        if sentiment is not None:
            extracted = _extract_sentiment(row.summary_data)
            if not extracted or sentiment_bucket(extracted["score"]) != sentiment:
                continue
        if media is not None and media not in _extract_media_types(row):
            continue
        filtered.append(row)
    return filtered


def matches_analytics_group(
    row: AIAnalytics, axis: str, value: str, entity_type: str | None = None, group_by_period: str | None = None
) -> bool:
    """Row membership for drill-down, including rows without topic_chain_id."""
    if axis == "themes":
        topics = _extract_topics(row.summary_data)
        return (not topics) if value == "(без темы)" else any(t.casefold() == value.casefold() for t in topics)
    if axis == "sources":
        return row.source_id == int(value)
    if axis == "entities":
        return any(
            ent["name"] == value and (not entity_type or ent["type"] == entity_type)
            for ent in _extract_entities(row.summary_data)
        )
    if axis == "sentiment":
        sent = _extract_sentiment(row.summary_data)
        return bool(sent and sentiment_bucket(sent["score"]) == value)
    if axis == "content_type":
        return value in _extract_media_types(row)
    if axis == "intent":
        return (_extract_intent(row.summary_data) or "unknown") == value
    if axis == "days":
        key = (
            _period_key(row.analysis_date, group_by_period)
            if group_by_period in ("week", "month") and row.analysis_date
            else (row.analysis_date.isoformat() if row.analysis_date else "unknown")
        )
        return key == value
    raise ValueError(f"Unknown drill-down axis: {axis}")


async def group_analytics(
    rows: list[AIAnalytics],
    axis: GroupingAxis | str,
    time_breakdown: bool = False,
    entity_type: str | None = None,
    group_by_period: str | None = None,
    sentiment: str | None = None,
    media: str | None = None,
) -> dict[str, Any]:
    """Group analytics rows by the chosen axis after sentiment/media filtering.

    Args:
        rows: List of AIAnalytics rows to group.
        axis: Grouping axis (GroupingAxis enum or its string value).
        time_breakdown: If True, include per-date sub-entries within each group.
        entity_type: Optional filter for ENTITIES axis. One of "brand", "person",
            "org". When None, all entity types are included. Ignored for other axes.
        group_by_period: For DAYS axis only. One of "day", "week", "month".
            Groups rows by the selected period instead of individual dates.

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
                "entries": [...]   # only if time_breakdown=True or axis==DAYS
            }
        ]
    }
    """
    if isinstance(axis, str):
        axis = GroupingAxis(axis)
    # Validate entity_type filter
    if entity_type is not None and axis != GroupingAxis.ENTITIES:
        raise ValueError(f"entity_type filter is only valid for ENTITIES axis, got axis={axis.value}")
    if entity_type is not None:
        valid_types = {"brand", "person", "org"}
        if entity_type not in valid_types:
            raise ValueError(f"entity_type must be one of {valid_types}, got '{entity_type}'")

    rows = filter_analytics(rows, sentiment=sentiment, media=media)
    if not rows:
        result: dict[str, Any] = {"axis": axis.value, "time_breakdown": time_breakdown, "groups": []}
        if axis == GroupingAxis.ENTITIES and entity_type:
            result["entity_type"] = entity_type
        result.update({k: v for k, v in (("sentiment", sentiment), ("media", media)) if v is not None})
        return result

    groups: dict[str, dict[str, Any]] = {}
    source_ids_needed: set[int] = set()

    for row in rows:
        key: str | None = None

        if axis == GroupingAxis.DAYS:
            if group_by_period in ("week", "month"):
                key = _period_key(row.analysis_date, group_by_period) if row.analysis_date else "unknown"
            else:
                key = row.analysis_date.isoformat() if row.analysis_date else "unknown"
            bucket = groups.setdefault(
                key,
                {"key": key, "count": 0, "scores": [], "entries": []},
            )
            bucket["count"] += 1
            bucket["entries"].append(row)
            sent = _extract_sentiment(row.summary_data)
            if sent and sent.get("score") is not None:
                bucket["scores"].append(float(sent["score"]))
            continue

        elif axis == GroupingAxis.THEMES:
            # Membership is every distinct topic, not only the first topic.
            # Use casefold for matching/grouping; keep the first display label.
            topics = _extract_topics(row.summary_data) or ["(без темы)"]
            seen = set()
            for topic in topics:
                canonical = topic.casefold()
                if canonical in seen:
                    continue
                seen.add(canonical)
                bucket = groups.setdefault(
                    canonical,
                    {"key": topic, "count": 0, "scores": [], "entries": defaultdict(list), "chain_id": None},
                )
                bucket["count"] += 1
                sent = _extract_sentiment(row.summary_data)
                if sent and sent.get("score") is not None:
                    bucket["scores"].append(float(sent["score"]))
                if bucket["chain_id"] is None and row.topic_chain_id:
                    bucket["chain_id"] = row.topic_chain_id
                if time_breakdown:
                    day = row.analysis_date.isoformat() if row.analysis_date else "unknown"
                    bucket["entries"][day].append(row)
            continue

        elif axis == GroupingAxis.SOURCES:
            key = str(row.source_id)
            source_ids_needed.add(row.source_id)

        elif axis == GroupingAxis.ENTITIES:
            seen_entities = set()
            for ent in _extract_entities(row.summary_data):
                if entity_type and ent["type"] != entity_type:
                    continue
                key = ent["name"]
                if key in seen_entities:
                    continue
                seen_entities.add(key)
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

        elif axis == GroupingAxis.TOPIC_CHAINS:
            if not row.topic_chain_id:
                continue
            key = row.topic_chain_id

        if key is None:
            continue

        bucket = groups.setdefault(
            key,
            {"key": key, "count": 0, "scores": [], "entries": defaultdict(list)},
        )
        if axis == GroupingAxis.TOPIC_CHAINS and "label" not in bucket:
            bucket["label"] = row.chain_label or human_chain_label(row.summary_data)
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
        if axis == GroupingAxis.SOURCES:
            group["source_id"] = int(key)
        if axis == GroupingAxis.DAYS:
            group["display_key"] = _format_period_key(bucket["key"], group_by_period)
        if axis == GroupingAxis.TOPIC_CHAINS:
            # Human-readable title (chain_label / first topic) with the raw
            # chain id kept alongside so templates can link to the chain page.
            group["chain_id"] = bucket["key"]
            group["key"] = bucket.get("label") or bucket["key"]
        elif axis == GroupingAxis.THEMES and bucket["chain_id"] is not None:
            # For themes, we have optionally collected a chain_id from the rows.
            group["chain_id"] = bucket["chain_id"]

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
        elif axis == GroupingAxis.DAYS and bucket.get("entries"):
            entries = [_serialize_row(r) for r in sorted(bucket["entries"], key=lambda r: r.analysis_date or date.min)]
            group["entries"] = entries
        result_groups.append(group)

    if axis == GroupingAxis.DAYS:
        result_groups.sort(key=lambda g: g["key"], reverse=True)
    else:
        result_groups.sort(key=lambda g: -g["count"])

    result: dict[str, Any] = {"axis": axis.value, "time_breakdown": time_breakdown, "groups": result_groups}
    if axis == GroupingAxis.ENTITIES and entity_type:
        result["entity_type"] = entity_type
    result.update({k: v for k, v in (("sentiment", sentiment), ("media", media)) if v is not None})
    return result
