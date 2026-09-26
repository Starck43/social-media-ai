"""Tools: list scenarios and assign them to sources."""

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


@tool(
    name="scenario_assign",
    description="Привязать сценарий к источнику. Источник будет анализироваться по этому сценарию.",
    confirm=True,
    parameters={
        "type": "object",
        "properties": {
            "source_id": {"type": "integer", "description": "ID источника"},
            "scenario_id": {
                "type": "integer",
                "description": "ID сценария (None = убрать привязку, использовать дефолтный)",
            },
        },
        "required": ["source_id"],
    },
)
async def scenario_assign(source_id: int, scenario_id: int | None = None) -> dict[str, Any]:
    from app.models import AgentScenario, Source

    source = await Source.objects.get(id=int(source_id))
    if source is None:
        return {"error": f"Source {source_id} not found"}

    if scenario_id is not None:
        scenario = await AgentScenario.objects.get(id=int(scenario_id))
        if scenario is None:
            return {"error": f"Scenario {scenario_id} not found"}
        if not scenario.is_active:
            return {"error": f"Scenario {scenario_id} is inactive"}
        await Source.objects.update_by_id(source.id, agent_scenario_id=scenario.id)
        return {
            "status": "assigned",
            "source_id": source.id,
            "scenario_id": scenario.id,
            "scenario_name": scenario.name,
        }

    # scenario_id=None → use default scenario for the tenant
    default_scenario = await AgentScenario.objects.get_default_scenario(tenant_id=source.tenant_id)

    await Source.objects.update_by_id(source.id, agent_scenario_id=None)
    if default_scenario:
        await Source.objects.update_by_id(source.id, agent_scenario_id=default_scenario.id)
        return {
            "status": "assigned_default",
            "source_id": source.id,
            "scenario_id": default_scenario.id,
            "scenario_name": default_scenario.name,
        }
    return {"status": "unlinked", "source_id": source.id, "message": "No default scenario found for this workspace"}
