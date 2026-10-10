"""
API endpoints for managing bot scenarios.

Bot scenarios define how AI should analyze content from sources,
including which analysis types to apply and what actions to take.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import require_model_perm
from app.models import AgentScenario, User
from app.schemas.scenario import (
    ScenarioCreate,
    ScenarioUpdate,
    ScenarioResponse,
)
from app.services.ai.scenario import PlanLimitError, scenario_service
from app.services.ai.scenario_schema import ScenarioSchemaError
from app.types import ActionType

router = APIRouter(tags=["scenarios"])


@router.post("/scenarios", response_model=ScenarioResponse)
async def create_scenario(
    request: ScenarioCreate,
    _user: User = Depends(require_model_perm("agentscenario", ActionType.CREATE)),
):
    """Create a new agent scenario."""
    try:
        scenario = await scenario_service.create_scenario(
            name=request.name,
            description=request.description,
            analysis_types=request.analysis_types,
            content_types=request.content_types,
            scope=request.scope,
            base_prompt=request.base_prompt or request.ai_prompt,
            media_overrides=request.media_overrides,
            summary_prompt=request.summary_prompt,
            is_active=request.is_active,
            max_tokens=request.max_tokens,
            output_schema=request.output_schema,
        )
    except PlanLimitError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ScenarioSchemaError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return ScenarioResponse(
        id=scenario.id,
        name=scenario.name,
        description=scenario.description,
        analysis_types=scenario.analysis_types or [],
        content_types=scenario.content_types or [],
        scope=scenario.scope,
        base_prompt=scenario.base_prompt,
        media_overrides=scenario.media_overrides,
        summary_prompt=scenario.summary_prompt,
        ai_prompt=scenario.ai_prompt,
        validation_warnings=getattr(request, "_validation_warnings", []),
        is_active=scenario.is_active,
        max_tokens=scenario.max_tokens,
        output_schema=scenario.output_schema,
        created_at=scenario.created_at.isoformat() if scenario.created_at else "",
        updated_at=scenario.updated_at.isoformat() if scenario.updated_at else "",
    )


@router.get("/scenarios", response_model=list[ScenarioResponse])
async def list_scenarios(
    is_active: Optional[bool] = None,
    _user: User = Depends(require_model_perm("agentscenario", ActionType.VIEW)),
):
    """List all bot scenarios with an optional filter by active status."""
    if is_active is True:
        scenarios = await scenario_service.get_active_scenarios()
    elif is_active is False:
        all_scenarios = await AgentScenario.objects.filter()
        scenarios = [s for s in all_scenarios if not s.is_active]
    else:
        scenarios = await AgentScenario.objects.filter()

    return [
        ScenarioResponse(
            id=s.id,
            name=s.name,
            description=s.description,
            analysis_types=s.analysis_types or [],
            content_types=s.content_types or [],
            scope=s.scope,
            base_prompt=s.base_prompt,
            media_overrides=s.media_overrides,
            summary_prompt=s.summary_prompt,
            ai_prompt=s.ai_prompt,
            is_active=s.is_active,
            max_tokens=s.max_tokens,
            output_schema=s.output_schema,
            created_at=s.created_at.isoformat() if s.created_at else "",
            updated_at=s.updated_at.isoformat() if s.updated_at else "",
        )
        for s in scenarios
    ]


@router.get("/scenarios/{scenario_id}", response_model=ScenarioResponse)
async def get_scenario(
    scenario_id: int,
    _user: User = Depends(require_model_perm("agentscenario", ActionType.VIEW)),
):
    """Get a specific bot scenario by ID."""
    scenario = await scenario_service.get_scenario_by_id(scenario_id)

    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")

    return ScenarioResponse(
        id=scenario.id,
        name=scenario.name,
        description=scenario.description,
        analysis_types=scenario.analysis_types or [],
        content_types=scenario.content_types or [],
        scope=scenario.scope,
        base_prompt=scenario.base_prompt,
        media_overrides=scenario.media_overrides,
        summary_prompt=scenario.summary_prompt,
        ai_prompt=scenario.ai_prompt,
        is_active=scenario.is_active,
        max_tokens=scenario.max_tokens,
        output_schema=scenario.output_schema,
        created_at=scenario.created_at.isoformat() if scenario.created_at else "",
        updated_at=scenario.updated_at.isoformat() if scenario.updated_at else "",
    )


@router.put("/scenarios/{scenario_id}", response_model=ScenarioResponse)
async def update_scenario(
    scenario_id: int,
    request: ScenarioUpdate,
    _user: User = Depends(require_model_perm("agentscenario", ActionType.UPDATE)),
):
    """Update a bot scenario — only provided fields are changed."""
    # Build update dict from non-None fields
    updates = {}
    if request.name is not None:
        updates["name"] = request.name
    if request.description is not None:
        updates["description"] = request.description
    if request.analysis_types is not None:
        updates["analysis_types"] = request.analysis_types
    if request.content_types is not None:
        updates["content_types"] = request.content_types
    if request.scope is not None:
        updates["scope"] = request.scope
    if request.base_prompt is not None or request.ai_prompt is not None:
        updates["base_prompt"] = request.base_prompt if request.base_prompt is not None else request.ai_prompt
    if request.media_overrides is not None:
        updates["media_overrides"] = request.media_overrides
    if request.summary_prompt is not None:
        updates["summary_prompt"] = request.summary_prompt
    if request.is_active is not None:
        updates["is_active"] = request.is_active
    if request.max_tokens is not None:
        updates["max_tokens"] = request.max_tokens
    if "output_schema" in request.model_fields_set:
        # Explicit null clears the custom schema; omission leaves it unchanged.
        updates["output_schema"] = request.output_schema

    try:
        scenario = await scenario_service.update_scenario(scenario_id, **updates)
    except ScenarioSchemaError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")

    return ScenarioResponse(
        id=scenario.id,
        name=scenario.name,
        description=scenario.description,
        analysis_types=scenario.analysis_types or [],
        content_types=scenario.content_types or [],
        scope=scenario.scope,
        base_prompt=scenario.base_prompt,
        media_overrides=scenario.media_overrides,
        summary_prompt=scenario.summary_prompt,
        ai_prompt=scenario.ai_prompt,
        validation_warnings=getattr(request, "_validation_warnings", []),
        is_active=scenario.is_active,
        max_tokens=scenario.max_tokens,
        output_schema=scenario.output_schema,
        created_at=scenario.created_at.isoformat() if scenario.created_at else "",
        updated_at=scenario.updated_at.isoformat() if scenario.updated_at else "",
    )


@router.delete("/scenarios/{scenario_id}")
async def delete_scenario(
    scenario_id: int,
    _user: User = Depends(require_model_perm("agentscenario", ActionType.DELETE)),
):
    """Delete a bot scenario."""
    deleted = await scenario_service.delete_scenario(scenario_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="Scenario not found")

    return {"status": "deleted", "scenario_id": scenario_id}
