"""Scenarios `/app/scenarios` — list and manage bot scenarios.

M3: list scenarios, set default, view details.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from app.core.database import new_session
from app.models.agent_scenario import AgentScenario
from app.models.source import Source

from .deps import add_flash, ensure_csrf, render

router = APIRouter(prefix="/scenarios")


@router.get("")
@router.get("/")
async def scenarios_list(request: Request):
    tenant_id = request.state.tenant_id

    async with new_session() as session:
        result = await session.execute(
            select(
                AgentScenario.id,
                AgentScenario.name,
                AgentScenario.description,
                AgentScenario.is_default,
                AgentScenario.is_active,
                AgentScenario.analysis_types,
                AgentScenario.content_types,
                func.count(Source.id).label("source_count"),
            )
            .outerjoin(Source, AgentScenario.id == Source.agent_scenario_id)
            .where(AgentScenario.tenant_id == tenant_id)
            .group_by(AgentScenario.id)
            .order_by(AgentScenario.is_default.desc(), AgentScenario.name)
        )
        rows = result.all()

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

    tenant_id = request.state.tenant_id

    async with new_session() as session:
        target = await session.execute(
            select(AgentScenario).where(AgentScenario.id == scenario_id, AgentScenario.tenant_id == tenant_id)
        )
        target_scenario = target.scalars().first()

        if target_scenario is None:
            add_flash(request, "error", "Сценарий не найден")
            return RedirectResponse("/app/scenarios", status_code=302)

        if not target_scenario.is_active:
            add_flash(request, "error", "Нельзя сделать активным неактивный сценарий")
            return RedirectResponse("/app/scenarios", status_code=302)

        # Clear is_default on all other scenarios for this tenant
        await session.execute(
            select(AgentScenario)
            .where(AgentScenario.tenant_id == tenant_id, AgentScenario.is_default == True)
            .update({"is_default": False}, synchronize_session=False)
        )

        target_scenario.is_default = True
        await session.commit()

    add_flash(request, "success", f"Сценарий '{target_scenario.name}' теперь используется по умолчанию")
    return RedirectResponse("/app/scenarios", status_code=302)
