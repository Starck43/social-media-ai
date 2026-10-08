"""Report tools expose compact aggregates, never raw analytics rows or prompts."""

from __future__ import annotations

from typing import Any

from app.agent.tools import tool
from app.services.ai.reporting import ReportAggregator

REPORT_AXES = ["themes", "sources", "entities", "intent", "topic_chains"]
LIMIT_SCHEMA = {
    "type": "integer",
    "minimum": 0,
    "default": 10,
    "description": "Maximum groups, default 10. Explicit 0 requests the full aggregate list.",
}
FILTER_SCHEMA = {
    "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]},
    "media": {"type": "string", "enum": ["text", "image", "video"]},
}


def compact_groups(groups: list[dict], limit: int = 10, *, chain_keys: bool = False) -> list[dict]:
    """Allowlist output keys even when the underlying service gains new fields."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("limit must be a non-negative integer (0 means all)")
    selected = groups if limit == 0 else groups[:limit]
    return [
        {
            "key": g.get("chain_id", g["key"]) if chain_keys else g["key"],
            "count": g["count"],
            "avg_sentiment": g.get("avg_sentiment"),
        }
        for g in selected
    ]


@tool(
    name="report_period",
    description="Сводка за day/week: компактные агрегаты без вызова LLM и без отправки в канал.",
    parameters={
        "type": "object",
        "properties": {
            "period": {"type": "string", "enum": ["day", "week"]},
            "group_by": {"type": "string", "enum": REPORT_AXES},
            "time_breakdown": {"type": "boolean", "description": "Include compact per-date aggregate slices"},
            "entity_type": {"type": "string", "enum": ["person", "brand", "org"]},
            "limit": LIMIT_SCHEMA,
            **FILTER_SCHEMA,
        },
        "required": [],
    },
    required_permission="digestrun.view",
)
async def report_period(
    period: str = "day",
    group_by: str = "themes",
    time_breakdown: bool = False,
    entity_type: str | None = None,
    sentiment: str | None = None,
    media: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    from app.services.digest.builder import period_bounds

    if period not in ("day", "week"):
        return {"error": f"Unknown period: {period!r}. Use day or week"}
    if group_by not in REPORT_AXES:
        return {"error": f"Unknown group_by: {group_by!r}. Use: {', '.join(REPORT_AXES)}"}
    start, end = period_bounds(period)
    try:
        result = await ReportAggregator().get_grouped_analytics(
            axis=group_by,
            days=(end - start).days + 1,
            time_breakdown=time_breakdown,
            entity_type=entity_type,
            sentiment=sentiment,
            media=media,
        )
        groups = compact_groups(result["groups"], limit)
        if time_breakdown:
            for compact, full in zip(groups, result["groups"]):
                compact["entries"] = [
                    {"date": e["date"], "count": e["count"], "avg_sentiment": e.get("avg_sentiment")}
                    for e in full.get("entries", [])
                ]
    except ValueError as exc:
        return {"error": str(exc)}
    return {
        "period": period,
        "group_by": group_by,
        "period_start": str(start),
        "period_end": str(end),
        "time_breakdown": time_breakdown,
        "sentiment": sentiment,
        "media": media,
        "groups": groups,
        "total": len(result["groups"]),
        "limit": limit,
    }


@tool(
    name="digest_send_now",
    description="Построить дайджест и отправить в настроенные каналы после подтверждения.",
    confirm=True,
    required_permission="digestrun.update",
    parameters={
        "type": "object",
        "properties": {
            "period": {"type": "string", "enum": ["day", "week"]},
            "group_by": {"type": "string", "enum": REPORT_AXES},
            "time_breakdown": {"type": "boolean"},
        },
        "required": [],
    },
)
async def digest_send_now(
    period: str = "day", group_by: str = "themes", time_breakdown: bool = False
) -> dict[str, Any]:
    from app.services.digest.builder import build_and_publish

    if period not in ("day", "week"):
        return {"error": f"Unknown period: {period!r}. Use day or week"}
    return await build_and_publish(period=period, group_by=group_by, time_breakdown=time_breakdown)


@tool(
    name="analytics_chains",
    description="Топ цепочек: key (chain_id), count и avg_sentiment. По умолчанию 10, без сырых анализов и без LLM.",
    parameters={
        "type": "object",
        "properties": {
            "source_id": {"type": "integer"},
            "days": {"type": "integer", "minimum": 1, "default": 30},
            "limit": LIMIT_SCHEMA,
            **FILTER_SCHEMA,
        },
        "required": [],
    },
    required_permission="aianalytics.view",
)
async def analytics_chains(
    source_id: int | None = None,
    days: int = 30,
    limit: int = 10,
    sentiment: str | None = None,
    media: str | None = None,
) -> dict[str, Any]:
    try:
        result = await ReportAggregator().get_grouped_analytics(
            axis="topic_chains", days=days, source_id=source_id, sentiment=sentiment, media=media
        )
        chains = compact_groups(result["groups"], limit, chain_keys=True)
    except ValueError as exc:
        return {"error": str(exc)}
    return {"chains": chains, "total": len(result["groups"]), "limit": limit}


@tool(
    name="analytics_chain_detail",
    description="Хронология цепочки как дневные агрегаты key (date), count, avg_sentiment; сырые строки не возвращаются.",
    parameters={
        "type": "object",
        "properties": {"chain_id": {"type": "string"}, "limit": LIMIT_SCHEMA},
        "required": ["chain_id"],
    },
    required_permission="aianalytics.view",
)
async def analytics_chain_detail(chain_id: str, limit: int = 10) -> dict[str, Any]:
    try:
        result = await ReportAggregator().get_grouped_analytics(
            axis="days", days=None, chain_id=chain_id, time_breakdown=True
        )
        groups = compact_groups(result["groups"], limit)
    except ValueError as exc:
        return {"error": str(exc)}
    if not result["groups"]:
        return {"error": f"Цепочка {chain_id!r} не найдена"}
    return {"chain_id": chain_id, "groups": groups, "total": len(result["groups"]), "limit": limit}
