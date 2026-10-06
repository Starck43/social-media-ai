"""Tools: build reports and trigger digests."""

from __future__ import annotations

from typing import Any

from app.agent.tools import tool


@tool(
    name="report_period",
    description=(
        "Собрать сводку активности в соцсетях за период (day/week) и вернуть её текст. "
        "Не отправляет в канал — только показывает вам."
    ),
    parameters={
        "type": "object",
        "properties": {
            "period": {"type": "string", "enum": ["day", "week"], "description": "Период (по умолчанию day)"},
        },
        "required": [],
    },
    required_permission="digestrun.view",
)
async def report_period(period: str = "day") -> dict[str, Any]:
    from app.services.digest.builder import aggregate, period_bounds
    from app.services.digest.render import render_plain

    if period not in ("day", "week"):
        return {"error": f"Unknown period: {period!r}. Use day or week"}

    data, start, end = await aggregate(period)
    text = render_plain(data)
    return {"period": period, "period_start": str(start), "period_end": str(end), "text": text}


@tool(
    name="digest_send_now",
    description=(
        "Построить сводку и отправить её в настроенные каналы (Telegram/MAX) прямо сейчас. "
        "То же самое, что ежедневная сводка по расписанию, но вручную."
    ),
    confirm=True,
    required_permission="digestrun.update",
    parameters={
        "type": "object",
        "properties": {
            "period": {"type": "string", "enum": ["day", "week"], "description": "Период (по умолчанию day)"},
        },
        "required": [],
    },
)
async def digest_send_now(period: str = "day") -> dict[str, Any]:
    from app.services.digest.builder import build_and_publish

    if period not in ("day", "week"):
        return {"error": f"Unknown period: {period!r}. Use day or week"}
    return await build_and_publish(period=period)


@tool(
    name="analytics_chains",
    description=(
        "Список тематических цепочек аналитики: label, число анализов, период, "
        "средняя тональность и топ-темы. Полезно, чтобы подсказать группировку "
        "нового анализа по уже существующим цепочкам."
    ),
    parameters={
        "type": "object",
        "properties": {
            "source_id": {"type": "integer", "description": "ID источника; пусто = все источники"},
            "days": {"type": "integer", "description": "Смотреть анализы за последние N дней (по умолчанию 30)"},
        },
        "required": [],
    },
    required_permission="aianalytics.view",
)
async def analytics_chains(source_id: int | None = None, days: int = 30) -> dict[str, Any]:
    from datetime import date, timedelta

    from app.models.managers.ai_analytics_manager import AIAnalyticsManager

    end = date.today()
    start = end - timedelta(days=days)
    chains = await AIAnalyticsManager().get_chains_summary(source_id=source_id, start_date=start, end_date=end)
    if not chains:
        return {"chains": [], "total": 0}
    return {"chains": chains, "total": len(chains)}


@tool(
    name="analytics_chain_detail",
    description=(
        "Хронология одной тематической цепочки: все анализы с датами, темами и "
        "метриками. Вход — topic_chain_id (см. analytics_chains)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "chain_id": {"type": "string", "description": "topic_chain_id из analytics_chains"},
        },
        "required": ["chain_id"],
    },
    required_permission="aianalytics.view",
)
async def analytics_chain_detail(chain_id: str) -> dict[str, Any]:
    from app.models.managers.ai_analytics_manager import AIAnalyticsManager

    detail = await AIAnalyticsManager().get_chain_detail(chain_id)
    if not detail:
        return {"error": f"Цепочка {chain_id!r} не найдена"}
    return detail
