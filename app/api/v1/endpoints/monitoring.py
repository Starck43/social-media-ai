"""Monitoring API endpoints for content collection."""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks

from app.models import Source, Platform, User
from app.services.monitoring.collector import ContentCollector
from app.services.user.auth import get_authenticated_user
from app.schemas.monitoring import (
    CollectRequest,
    CollectPlatformRequest,
    CollectMonitoredRequest,
)

router = APIRouter(tags=["monitoring"])


@router.post("/collect/source")
async def collect_from_source(
	request: CollectRequest,
	background_tasks: BackgroundTasks,
	current_user: User = Depends(get_authenticated_user)
):
	"""
	Collect content from a single source.
	
	This endpoint triggers content collection from a specified source.
	"""
	if not current_user.is_superuser:
		raise HTTPException(status_code=403, detail="Admin access required")
	
	# Verify source exists
	source = await Source.objects.get(id=request.source_id)
	if not source:
		raise HTTPException(status_code=404, detail="Source not found")
	
	# Run collection in background
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
	current_user: User = Depends(get_authenticated_user)
):
	"""
	Collect content from all active sources on a platform.
	
	Optionally filter by source types.
	"""
	if not current_user.is_superuser:
		raise HTTPException(status_code=403, detail="Admin access required")
	
	# Verify a platform exists
	platform = await Platform.objects.get(id=request.platform_id)
	if not platform:
		raise HTTPException(status_code=404, detail="Platform not found")
	
	# Run collection in background
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
	current_user: User = Depends(get_authenticated_user)
):
	"""
	Collect content from monitored users of a source.
	
	This is useful for GROUP/CHANNEL sources that track specific USER accounts.
	"""
	if not current_user.is_superuser:
		raise HTTPException(status_code=403, detail="Admin access required")
	
	# Verify source exists
	source = await Source.objects.get(id=request.source_id)
	if not source:
		raise HTTPException(status_code=404, detail="Source not found")
	
	# Run collection in background
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
	current_user: User = Depends(get_authenticated_user)
):
	"""
	Get AI analytics for a specific source.
	"""
	from app.models import AIAnalytics
	
	# Verify source exists
	source = await Source.objects.get(id=source_id)
	if not source:
		raise HTTPException(status_code=404, detail="Source not found")
	
	# Get latest analytics
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
				"analysis_date": a.analysis_date,
				"period_type": str(a.period_type) if a.period_type else None,
				"topic_chain_id": a.topic_chain_id,
				"llm_model": a.llm_model,
				"summary_data": a.summary_data,
				"created_at": a.created_at
			}
			for a in analytics
		]
	}
