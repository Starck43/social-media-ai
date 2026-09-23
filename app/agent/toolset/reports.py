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
