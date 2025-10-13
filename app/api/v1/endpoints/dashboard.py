"""Dashboard API endpoints for statistics and summaries."""

import logging
from datetime import date, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query

from app.models import User, Source, AIAnalytics, Platform, Notification
from app.services.user.auth import get_authenticated_user
from app.types.models import SourceType, PeriodType
from app.schemas.dashboard import (
    DashboardStats,
    SourceSummary,
    AnalyticsSummary,
    TrendData,
)

router = APIRouter(tags=["dashboard"])
logger = logging.getLogger(__name__)


@router.get("/dashboard/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    platform_id: Optional[int] = None,
    source_type: Optional[SourceType] = None,
    since: Optional[date] = Query(None, description="Stats since this date"),
    current_user: User = Depends(get_authenticated_user),
):
    """
    Get dashboard statistics with optional filters.

    Returns comprehensive statistics about sources, platforms, analytics and notifications.
    """
    logger.info(
        f"User {current_user.username} requesting dashboard stats "
        f"(platform={platform_id}, source_type={source_type}, since={since})"
    )

    # Get sources with filters
    source_query = Source.objects.filter()
    if platform_id:
        source_query = source_query.filter(platform_id=platform_id)
    if source_type:
        source_query = source_query.filter(source_type=source_type.name)

    sources = await source_query
    active_sources = [s for s in sources if s.is_active]

    # Get platforms
    platforms = await Platform.objects.filter()
    active_platforms = [p for p in platforms if p.is_active]

    # Get analytics with date filter
    analytics_query = AIAnalytics.objects.filter()
    if since:
        analytics_query = analytics_query.filter(analysis_date__gte=since)
    analytics = await analytics_query

    # Get unread notifications
    unread_notifications = await Notification.objects.filter(is_read=False)

    # Count by platform
    sources_by_platform = {}
    for source in sources:
        platform_name = f"Platform {source.platform_id}"
        # Try to find platform name
        for p in platforms:
            if p.id == source.platform_id:
                platform_name = p.name
                break
        sources_by_platform[platform_name] = (
            sources_by_platform.get(platform_name, 0) + 1
        )

    # Count by source type
    sources_by_type = {}
    for source in sources:
        stype = str(source.source_type) if source.source_type else "unknown"
        sources_by_type[stype] = sources_by_type.get(stype, 0) + 1

    # Count analytics by period
    analytics_by_period = {}
    for analytic in analytics:
        period = str(analytic.period_type) if analytic.period_type else "unknown"
        analytics_by_period[period] = analytics_by_period.get(period, 0) + 1

    return DashboardStats(
        total_sources=len(sources),
        active_sources=len(active_sources),
        total_platforms=len(platforms),
        active_platforms=len(active_platforms),
        total_analytics=len(analytics),
        unread_notifications=len(unread_notifications),
        sources_by_platform=sources_by_platform,
        sources_by_type=sources_by_type,
        analytics_by_period=analytics_by_period,
    )


@router.get("/dashboard/sources", response_model=List[SourceSummary])
async def get_sources_summary(
    platform_id: Optional[int] = None,
    source_type: Optional[SourceType] = None,
    is_active: Optional[bool] = None,
    has_scenario: Optional[bool] = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_authenticated_user),
):
    """
    Get sources summary with filters.

    Filters:
    - platform_id: Filter by platform
    - source_type: Filter by source type
    - is_active: Filter by active status
    - has_scenario: Filter sources with/without bot scenario
    - limit/offset: Pagination
    """
    logger.info(
        f"User {current_user.username} requesting sources summary "
        f"(platform={platform_id}, type={source_type}, active={is_active})"
    )

    # Build query
    query = Source.objects.filter()

    if platform_id:
        query = query.filter(platform_id=platform_id)
    if source_type:
        query = query.filter(source_type=source_type.name)
    if is_active is not None:
        query = query.filter(is_active=is_active)

    sources = await query.order_by(Source.updated_at.desc()).offset(offset).limit(limit)

    # Get all platforms for name resolution
    platforms = await Platform.objects.filter()
    platform_map = {p.id: p.name for p in platforms}

    # Get analytics count per source
    all_analytics = await AIAnalytics.objects.filter()
    analytics_count = {}
    for a in all_analytics:
        analytics_count[a.source_id] = analytics_count.get(a.source_id, 0) + 1

    # Filter by scenario if needed
    if has_scenario is not None:
        sources = [
            s
            for s in sources
            if (s.bot_scenario_id is not None) == has_scenario
        ]

    # Get bot scenarios
    from app.models import BotScenario

    scenarios = await BotScenario.objects.filter()
    scenario_map = {s.id: s.name for s in scenarios}

    result = []
    for source in sources:
        result.append(
            SourceSummary(
                id=source.id,
                name=source.name,
                platform_name=platform_map.get(source.platform_id, f"Platform {source.platform_id}"),
                source_type=str(source.source_type) if source.source_type else "unknown",
                is_active=source.is_active,
                last_checked=source.last_checked.isoformat()
                if source.last_checked
                else None,
                analytics_count=analytics_count.get(source.id, 0),
                bot_scenario_name=scenario_map.get(source.bot_scenario_id)
                if source.bot_scenario_id
                else None,
            )
        )

    return result


@router.get("/dashboard/analytics", response_model=List[AnalyticsSummary])
async def get_analytics_summary(
    source_id: Optional[int] = None,
    period_type: Optional[PeriodType] = None,
    since: Optional[date] = Query(None, description="Show analytics since this date"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_authenticated_user),
):
    """
    Get analytics summary with filters.

    Filters:
    - source_id: Filter by source
    - period_type: Filter by period type
    - since: Show analytics created after this date
    - limit/offset: Pagination
    """
    logger.info(
        f"User {current_user.username} requesting analytics summary "
        f"(source={source_id}, period={period_type}, since={since})"
    )

    # Build query
    query = AIAnalytics.objects.filter()

    if source_id:
        query = query.filter(source_id=source_id)
    if period_type:
        query = query.filter(period_type=period_type.value)
    if since:
        query = query.filter(analysis_date__gte=since)

    analytics = await (
        query.order_by(AIAnalytics.created_at.desc()).offset(offset).limit(limit)
    )

    # Get sources for name resolution
    sources = await Source.objects.filter()
    source_map = {s.id: s.name for s in sources}

    return [
        AnalyticsSummary(
            id=a.id,
            source_id=a.source_id,
            source_name=source_map.get(a.source_id, f"Source {a.source_id}"),
            analysis_date=a.analysis_date.isoformat() if a.analysis_date else "",
            period_type=str(a.period_type) if a.period_type else "unknown",
            topic_chain_id=a.topic_chain_id,
            llm_model=a.llm_model,
            created_at=a.created_at.isoformat() if a.created_at else "",
        )
        for a in analytics
    ]


@router.get("/dashboard/trends/{source_id}", response_model=List[TrendData])
async def get_source_trends(
    source_id: int,
    days: int = Query(30, ge=1, le=365, description="Number of days to analyze"),
    metric: str = Query("sentiment", description="Metric to track (sentiment, activity, engagement)"),
    current_user: User = Depends(get_authenticated_user),
):
    """
    Get trend data for a specific source.

    Tracks selected metric over time.
    """
    logger.info(
        f"User {current_user.username} requesting trends for source {source_id} "
        f"(days={days}, metric={metric})"
    )

    # Verify source exists
    source = await Source.objects.get(id=source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    # Get analytics for the period
    start_date = date.today() - timedelta(days=days)
    analytics = await AIAnalytics.objects.filter(
        source_id=source_id, analysis_date__gte=start_date
    ).order_by(AIAnalytics.analysis_date.asc())

    if not analytics:
        logger.info(f"No analytics data found for source {source_id}")
        return []

    # Extract trend data based on metric
    trends = []
    for a in analytics:
        value = 0.0
        label = ""

        if a.summary_data:
            if metric == "sentiment":
                # Extract sentiment score
                ai_analysis = a.summary_data.get("ai_analysis", {})
                sentiment = ai_analysis.get("sentiment_analysis", {})
                value = sentiment.get("sentiment_score", 0.0)
                label = sentiment.get("overall_sentiment", "neutral")

            elif metric == "activity":
                # Extract activity metrics
                content_stats = a.summary_data.get("content_statistics", {})
                value = float(content_stats.get("total_posts", 0))
                label = f"{int(value)} posts"

            elif metric == "engagement":
                # Extract engagement rate
                content_stats = a.summary_data.get("content_statistics", {})
                value = content_stats.get("avg_reactions_per_post", 0.0)
                label = f"{value:.1f} avg reactions"

        trends.append(
            TrendData(
                date=a.analysis_date.isoformat() if a.analysis_date else "",
                value=value,
                label=label,
            )
        )

    return trends


@router.get("/dashboard/notifications/recent", response_model=List)
async def get_recent_notifications(
    limit: int = Query(10, ge=1, le=50),
    current_user: User = Depends(get_authenticated_user),
):
    """Get recent notifications for dashboard display."""
    logger.info(f"User {current_user.username} requesting recent notifications")

    notifications = await (
        Notification.objects.filter()
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )

    return [
        {
            "id": n.id,
            "title": n.title,
            "message": n.message[:100] + "..." if len(n.message) > 100 else n.message,
            "type": str(n.notification_type) if n.notification_type else "unknown",
            "is_read": n.is_read,
            "created_at": n.created_at.isoformat() if n.created_at else "",
        }
        for n in notifications
    ]
