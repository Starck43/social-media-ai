"""Monitoring API endpoints for content collection."""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks

from app.api.deps import require_model_perm
from app.models import Source, Platform
from app.services.monitoring.collector import ContentCollector
from app.schemas.monitoring import CollectRequest, CollectPlatformRequest, CollectMonitoredRequest
from app.types import ActionType

router = APIRouter(tags=["monitoring"])


@router.post("/collect/source")
async def collect_from_source(
    request: CollectRequest,
    background_tasks: BackgroundTasks,
    _user = Depends(require_model_perm("source", ActionType.UPDATE)),
):
    """Trigger content collection from a specified source."""
    source = await Source.objects.get(id=request.source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    collector = ContentCollector()
    background_tasks.add_task(
        collector.collect_from_source,
        source=source,
        content_type=request.content_type,
        analyze=request.analyze
    )

    return {
        "status": "started",
        "source_id": request.source_id,
        "message": "Content collection started in background"
    }


@router.post("/collect/platform")
async def collect_from_platform(
    request: CollectPlatformRequest,
    background_tasks: BackgroundTasks,
    _user = Depends(require_model_perm("source", ActionType.UPDATE)),
):
    """Collect content from all active sources on a platform."""
    platform = await Platform.objects.get(id=request.platform_id)
    if not platform:
        raise HTTPException(status_code=404, detail="Platform not found")

    collector = ContentCollector()
    background_tasks.add_task(
        collector.collect_from_platform,
        platform_id=request.platform_id,
        source_types=request.source_types,
        analyze=request.analyze
    )

    return {
        "status": "started",
        "platform_id": request.platform_id,
        "message": "Platform content collection started in background"
    }


@router.post("/collect/monitored")
async def collect_monitored_users(
    request: CollectMonitoredRequest,
    background_tasks: BackgroundTasks,
    _user = Depends(require_model_perm("source", ActionType.UPDATE)),
):
    """Collect content from monitored users of a source."""
    source = await Source.objects.get(id=request.source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    collector = ContentCollector()
    background_tasks.add_task(
        collector.collect_monitored_users,
        source=source,
        analyze=request.analyze
    )

    return {
        "status": "started",
        "source_id": request.source_id,
        "message": "Monitored users collection started in background"
    }


@router.get("/analytics/source/{source_id}")
async def get_source_analytics(
    source_id: int,
    _user = Depends(require_model_perm("source", ActionType.VIEW)),
):
    """Get AI analytics for a specific source."""
    from app.models import AIAnalytics

    source = await Source.objects.get(id=source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    analytics = await (
        AIAnalytics.objects
        .filter(source_id=source_id)
        .order_by(AIAnalytics.created_at.desc())
        .limit(10)
    )

    return {
        "source_id": source_id,
        "source_name": source.name,
        "analytics": [
            {
                "id": a.id,
                "analysis_date": a.analysis_date.isoformat() if a.analysis_date else None,
                "period_type": str(a.period_type) if a.period_type else None,
                "topic_chain_id": a.topic_chain_id,
                "llm_model": a.llm_model,
                "summary_data": a.summary_data,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in analytics
        ]
    }
