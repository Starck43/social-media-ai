"""
API endpoints for managing bot scenarios.

Bot scenarios define how AI should analyze content from sources,
including which analysis types to apply and what actions to take.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.models import AgentScenario, User
from app.schemas.scenario import (
    ScenarioCreate,
    ScenarioUpdate,
    ScenarioResponse,
)
from app.services.ai.scenario import PlanLimitError, scenario_service
from app.services.user.auth import get_authenticated_user

router = APIRouter(tags=["scenarios"])


@router.post("/scenarios", response_model=ScenarioResponse)
async def create_scenario(
    request: ScenarioCreate,
    current_user: User = Depends(get_authenticated_user)
):
    """
    Create a new bot scenario.
    
    The analysis_types and content_types are now separate from scope.
    Scope contains only configuration parameters for selected analysis types.
    
    Example:
        ```json
        {
            "name": “Sentiment Monitoring”,
            “description”: "Track customer sentiment",
            "analysis_types": [“sentiment”, “keywords”],
            "content_types": [“posts”, “comments”],
            “scope”: {
                "sentiment_config": {
                    "categories": [“positive”, “negative”, “neutral”]
                }
            },
            "ai_prompt": "Analyze sentiment: {content}",
        }
        ```
    
    Admin access required.
    """
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin access required")

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
        # A quota is not a permission problem, but the client did ask for
        # something it cannot have; 403 with the reason is what a client can
        # show. The message names the tier and the count on purpose.
        raise HTTPException(status_code=403, detail=str(e)) from e

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
    current_user: User = Depends(get_authenticated_user)
):
    """
    List all bot scenarios with an optional filter by active status.
    
    Returns scenarios with their analysis_types, content_types, and scope.
    Pass is_active=true to get only active scenarios, is_active=false for inactive,
    or omit to get all scenarios.
    """
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
    current_user: User = Depends(get_authenticated_user)
):
    """
    Get a specific bot scenario by ID.
    
    Returns full scenario details including analysis configuration.
    Useful for viewing or editing a scenario.
    """
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
    current_user: User = Depends(get_authenticated_user)
):
    """
    Update a bot scenario.
    
    All fields are optional — only provided fields will be updated.
    You can update analysis_types, content_types, and scope independently.
    
    Example partial update:
        ```json
        {
            "analysis_types": [“sentiment”, “keywords”, “topics”],
            “scope”: {
                "topics_config": {"max_topics": 10}
            }
        }
        ```
    
    Admin access required.
    """
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin access required")

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
    if request.output_schema is not None:
        updates["output_schema"] = request.output_schema

    scenario = await scenario_service.update_scenario(scenario_id, **updates)

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
    current_user: User = Depends(get_authenticated_user)
):
    """
    Delete a bot scenario.

    Scenario deletion no longer touches sources — a source does not reference a
    scenario (the scenario belongs to the task). Tasks referencing the scenario
    keep the FK with `ondelete=SET NULL` and fall back to the tenant default.

    Admin access required.
    """
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin access required")

    deleted = await scenario_service.delete_scenario(scenario_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="Scenario not found")

    return {"status": "deleted", "scenario_id": scenario_id}
