"""Tools: list scenarios."""

from __future__ import annotations

from typing import Any

from app.agent.tools import tool


@tool(
    name="scenario_list",
    description="Показать список сценариев бота: id, название, является ли дефолтным, активен, типы анализа и контента.",
    parameters={
        "type": "object",
        "properties": {
            "only_active": {"type": "boolean", "description": "Только активные (по умолчанию true)"},
        },
        "required": [],
    },
)
async def scenario_list(only_active: bool = True) -> list[dict[str, Any]]:
    from app.models import AgentScenario

    qs = AgentScenario.objects.filter(is_active=True) if only_active else AgentScenario.objects.all()
    rows = await qs
    return [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description or "",
            "is_default": s.is_default,
            "is_active": s.is_active,
            "analysis_types": s.analysis_types or [],
            "content_types": s.content_types or [],
        }
        for s in rows
    ]
