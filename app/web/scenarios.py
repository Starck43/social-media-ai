"""Scenarios `/app/scenarios` — list and manage bot scenarios.

M3: list scenarios, set default, view details.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func

from app.models.agent_scenario import AgentScenario
from app.models.source import Source

from .deps import add_flash, ensure_csrf, render

router = APIRouter(prefix="/scenarios")


@router.get("")
@router.get("/")
async def scenarios_list(request: Request):
    """Scenarios with their source counts (LEFT JOIN — a scenario may have none)."""
    # No `tenant_id` predicate: the manager's guard already scopes the rows.
    rows = await (
        AgentScenario.objects.outerjoin(Source, AgentScenario.id == Source.agent_scenario_id)
        .values(
            AgentScenario.id,
            AgentScenario.name,
            AgentScenario.description,
            AgentScenario.is_default,
            AgentScenario.is_active,
            AgentScenario.analysis_types,
            AgentScenario.content_types,
            func.count(Source.id).label("source_count"),
        )
        .group_by(AgentScenario.id)
        .order_by(AgentScenario.is_default.desc(), AgentScenario.name)
        .rows()
    )

    scenarios = [
        {
            "id": r.id,
            "name": r.name,
            "description": r.description or "",
            "is_default": r.is_default,
            "is_active": r.is_active,
            "analysis_types": r.analysis_types or [],
            "content_types": r.content_types or [],
            "source_count": r.source_count,
        }
        for r in rows
    ]
    return render(request, "web/scenarios.html", section="scenarios", scenarios=scenarios)


@router.post("/set-default")
async def scenarios_set_default(
    request: Request,
    scenario_id: int = Form(...),
    token: str = Form("", alias="_csrf"),
):
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/scenarios", status_code=302)

    # The manager's tenant guard scopes the lookup, so a foreign id reads as
    # "not found" instead of leaking another workspace's scenario.
    target_scenario = await AgentScenario.objects.get(id=scenario_id)

    if target_scenario is None:
        add_flash(request, "error", "Сценарий не найден")
        return RedirectResponse("/app/scenarios", status_code=302)

    if not target_scenario.is_active:
        add_flash(request, "error", "Нельзя сделать активным неактивный сценарий")
        return RedirectResponse("/app/scenarios", status_code=302)

    # Clear is_default on every other scenario, then set this one. Two bulk
    # statements instead of a hand-built select().update() on a private session.
    await AgentScenario.objects.filter(is_default=True).update(is_default=False)
    await AgentScenario.objects.update_by_id(scenario_id, is_default=True)

    add_flash(request, "success", f"Сценарий '{target_scenario.name}' теперь используется по умолчанию")
    return RedirectResponse("/app/scenarios", status_code=302)
