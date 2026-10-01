from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, Sequence, Any

from sqlalchemy import select

from ...types import PeriodType
from .base_manager import BaseManager


class AIAnalyticsManager(BaseManager):
    """Manager for AI analytics operations.

    No method takes a session: reads go through the queryset, so the tenant
    guard in `BaseManager` applies and a caller cannot accidentally read another
    workspace's analytics. The previous signatures took a caller-owned
    `AsyncSession` and ran hand-built `select()` statements on it, which
    bypassed that guard entirely.
    """

    def __init__(self):
        # Use string literal to avoid circular import
        from ..ai_analytics import AIAnalytics

        super().__init__(AIAnalytics)

    async def get_by_source_id(self, source_id: int, skip: int = 0, limit: int = 100) -> Sequence[Any]:
        """Retrieve analytics by source ID with pagination."""
        return await (
            self.filter(source_id=source_id)
            .order_by(self.model.analysis_date.desc())
            .offset(skip)
            .limit(limit)
        )

    async def get_latest_by_source_id(self, source_id: int) -> Optional[Any]:
        """Get latest analytics for a source."""
        return await self.filter(source_id=source_id).order_by(self.model.analysis_date.desc()).first()

    async def get_by_date_range(
        self,
        source_id: int,
        start_date: date,
        end_date: date,
        period_type: PeriodType = PeriodType.DAILY,
    ) -> Sequence[Any]:
        """
        Retrieve analytics for a source within date range.

        Args:
                source_id: ID of the source
                start_date: Start date of the period
                end_date: End date of the period
                period_type: Type of period ('daily', 'weekly')

        Returns:
                List of AIAnalytics objects
        """
        return await self.filter(
            source_id=source_id,
            analysis_date__gte=start_date,
            analysis_date__lte=end_date,
            period_type=period_type,
        ).order_by(self.model.analysis_date)

    async def get_daily_summary(self, analysis_date: date) -> Sequence[Any]:
        """Get all daily summaries for a specific date."""
        return await self.filter(
            analysis_date=analysis_date, period_type=PeriodType.DAILY
        ).order_by(self.model.source_id)

    async def save_analysis(
        self,
        source_id: int,
        analysis_date: date,
        summary_data: dict,
        period_type: PeriodType = PeriodType.DAILY,
        topic_chain_id: Optional[str] = None,
        parent_analysis_id: Optional[int] = None,
        llm_model: Optional[str] = None,
        prompt_text: Optional[str] = None,
        response_payload: Optional[dict] = None,
    ) -> Any:
        """
        Save AI analysis results with full LLM tracing.

        Upserts on (source, date, period): an existing row is updated in place,
        otherwise a new one is created. `create()` stamps `tenant_id` from the
        ambient scope, so callers must run inside `tenant_scope(...)`.

        Returns:
                The created or updated AIAnalytics object
        """
        existing = await self.get(source_id=source_id, analysis_date=analysis_date, period_type=period_type)

        updates: dict[str, Any] = {"summary_data": summary_data}
        if topic_chain_id:
            updates["topic_chain_id"] = topic_chain_id
        if parent_analysis_id:
            updates["parent_analysis_id"] = parent_analysis_id
        if llm_model:
            updates["llm_model"] = llm_model
        if prompt_text:
            updates["prompt_text"] = prompt_text
        if response_payload:
            updates["response_payload"] = response_payload

        if existing:
            return await self.update_by_id(existing.id, **updates)

        return await self.create(
            source_id=source_id,
            analysis_date=analysis_date,
            period_type=period_type,
            **updates,
        )

    async def get_trends(self, source_id: int, days: int = 30) -> dict:
        """
        Get trends analysis for a source over time.

        Args:
                source_id: ID of the source
                days: Amount days to analyze

        Returns:
                Dictionary with trend analysis
        """
        end_date: date = date.today()
        start_date: date = end_date - timedelta(days=days)

        analytics = await self.get_by_date_range(source_id, start_date, end_date)

        if not analytics:
            return {}

        # Extract trend data from summary_data
        trends = {
            "mood_trend": [],
            "activity_trend": [],
            "topics_evolution": [],
            "period": {"start": start_date, "end": end_date},
        }

        for analysis in analytics:
            summary = analysis.summary_data

            # Mood trend
            if "mood_analysis" in summary:
                mood_data = summary["mood_analysis"]
                trends["mood_trend"].append(
                    {
                        "date": analysis.analysis_date,
                        "positive": mood_data.get("positive", 0),
                        "negative": mood_data.get("negative", 0),
                        "neutral": mood_data.get("neutral", 0),
                    }
                )

            # Activity trend
            if "activity_metrics" in summary:
                activity_data = summary["activity_metrics"]
                trends["activity_trend"].append(
                    {
                        "date": analysis.analysis_date,
                        "messages_count": activity_data.get("messages_count", 0),
                        "active_users": activity_data.get("active_users", 0),
                        "engagement_rate": activity_data.get("engagement_rate", 0),
                    }
                )

            # Topics evolution
            if "top_topics" in summary:
                topics_data = summary["top_topics"]
                trends["topics_evolution"].append({"date": analysis.analysis_date, "topics": topics_data})

        return trends

    async def get_by_topic_chain(self, topic_chain_id: str) -> Sequence[Any]:
        """
        Get all analytics in a topic chain.

        Args:
                topic_chain_id: Chain ID to filter by

        Returns:
                List of AIAnalytics objects in the chain
        """
        return await self.filter(topic_chain_id=topic_chain_id).order_by(self.model.analysis_date)

    async def get_children(self, parent_id: int) -> Sequence[Any]:
        """
        Get child analytics for a parent analysis.

        Args:
                parent_id: Parent analysis ID

        Returns:
                List of child AIAnalytics objects
        """
        return await self.filter(parent_analysis_id=parent_id).order_by(self.model.analysis_date)

    async def get_sources_without_recent_analysis(self, days: int = 1) -> Sequence[int]:
        """
        Get source IDs that haven't been analyzed in the specified days.

        Reads through `Source.objects`, so the result is limited to the ambient
        workspace — the old version executed a bare `select(Source.id)` on the
        caller's session and would have returned every workspace's sources.

        Args:
                days: Amount days to check back

        Returns:
                List of source IDs that need analysis
        """
        from ..source import Source

        cutoff_date: date = date.today() - timedelta(days=days)

        # Subquery over the analytics of the last `days` days
        recent = select(self.model.source_id).where(self.model.analysis_date >= cutoff_date)

        # Active sources that have no recent analysis
        rows = await Source.objects.filter(is_active=True).exclude(Source.id.in_(recent)).values(Source.id).rows()
        return [row[0] for row in rows]
