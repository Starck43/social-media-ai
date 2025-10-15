"""
API endpoints for managing bot scenarios.

Bot scenarios define how AI should analyze content from sources,
including which analysis types to apply and what actions to take.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.models import User, BotScenario
from app.schemas.scenario import (
    ScenarioCreate,
    ScenarioUpdate,
    ScenarioResponse,
    ScenarioAssign,
    ScenarioSourcesResponse,
)
from app.services.ai.scenario import scenario_service
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
            "action_type": “NOTIFICATION”
        }
        ```
    
    Admin access required.
    """
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin access required")

    scenario = await scenario_service.create_scenario(
        name=request.name,
        description=request.description,
        analysis_types=request.analysis_types,
        content_types=request.content_types,
        scope=request.scope,
        ai_prompt=request.ai_prompt,
        trigger_type=request.trigger_type,
        trigger_config=request.trigger_config,
        action_type=request.action_type,
        is_active=request.is_active,
        collection_interval_hours=request.collection_interval_hours,
    )

    return ScenarioResponse(
        id=scenario.id,
        name=scenario.name,
        description=scenario.description,
        analysis_types=scenario.analysis_types or [],
        content_types=scenario.content_types or [],
        scope=scenario.scope,
        ai_prompt=scenario.ai_prompt,
        action_type=scenario.action_type,
        is_active=scenario.is_active,
        collection_interval_hours=scenario.collection_interval_hours,
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
        all_scenarios = await BotScenario.objects.filter()
        scenarios = [s for s in all_scenarios if not s.is_active]
    else:
        scenarios = await BotScenario.objects.filter()

    return [
        ScenarioResponse(
            id=s.id,
            name=s.name,
            description=s.description,
            analysis_types=s.analysis_types or [],
            content_types=s.content_types or [],
            scope=s.scope,
            ai_prompt=s.ai_prompt,
            action_type=s.action_type,
            is_active=s.is_active,
            collection_interval_hours=s.collection_interval_hours,
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
        ai_prompt=scenario.ai_prompt,
        action_type=scenario.action_type,
        is_active=scenario.is_active,
        collection_interval_hours=scenario.collection_interval_hours,
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
    if request.ai_prompt is not None:
        updates["ai_prompt"] = request.ai_prompt
    if request.trigger_type is not None:
        updates["trigger_type"] = request.trigger_type
    if request.trigger_config is not None:
        updates["trigger_config"] = request.trigger_config
    if request.action_type is not None:
        updates["action_type"] = request.action_type
    if request.is_active is not None:
        updates["is_active"] = request.is_active
    if request.collection_interval_hours is not None:
        updates["collection_interval_hours"] = request.collection_interval_hours

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
        ai_prompt=scenario.ai_prompt,
        action_type=scenario.action_type,
        is_active=scenario.is_active,
        collection_interval_hours=scenario.collection_interval_hours,
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
    
    Note: Sources using this scenario will have their bot_scenario_id set to NULL
    (preserved by CASCADE behavior defined in the database).
    
    Admin access required.
    """
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin access required")

    deleted = await scenario_service.delete_scenario(scenario_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="Scenario not found")

    return {"status": "deleted", "scenario_id": scenario_id}


@router.post("/scenarios/assign")
async def assign_scenario(
    request: ScenarioAssign,
    current_user: User = Depends(get_authenticated_user)
):
    """
    Assign or remove a bot scenario from a source.
    
    When a scenario is assigned, all future content collection from that source
    will use the scenario's analysis configuration.
    
    Set scenario_id to null to remove the assignment and return to default analysis.
    
    Example:
        ```json
        {
            "source_id": 123,
            "scenario_id": 456
        }
        ```
    
    Admin access required.
    """
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin access required")

    source = await scenario_service.assign_scenario_to_source(request.source_id, request.scenario_id)

    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    return {
        "status": "assigned" if request.scenario_id else "removed",
        "source_id": source.id,
        "scenario_id": request.scenario_id,
    }


@router.get("/scenarios/{scenario_id}/sources", response_model=ScenarioSourcesResponse)
async def get_scenario_sources(
    scenario_id: int,
    is_active: Optional[bool] = True,
    current_user: User = Depends(get_authenticated_user)
):
    """
    Get all sources now using a specific scenario.
    
    Useful for understanding, which sources will be affected by scenario changes.
    Pass is_active=true for active sources only, false for inactive, or null for all.
    """
    # Verify scenario exists
    scenario = await scenario_service.get_scenario_by_id(scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")

    sources = await scenario_service.get_sources_by_scenario(scenario_id, is_active)

    return ScenarioSourcesResponse(
        scenario_id=scenario_id,
        scenario_name=scenario.name,
        sources=[
            {
                "id": s.id,
                "name": s.name,
                "external_id": s.external_id,
                "source_type": str(s.source_type) if s.source_type else None,
                "is_active": s.is_active,
            }
            for s in sources
        ],
    )
