from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, Sequence, TYPE_CHECKING, Any

from sqlalchemy import select, and_, desc, Row, RowMapping
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..ai_analytics import AIAnalytics


class AIAnalyticsManager(BaseManager):
    """Manager for AI analytics operations."""

    def __init__(self):
        # Use string literal to avoid circular import
        from ..ai_analytics import AIAnalytics

        super().__init__(AIAnalytics)

    async def get_by_source_id(
        self, db: AsyncSession, source_id: Mapped[int], skip: int = 0, limit: int = 100
    ) -> Sequence[AIAnalytics]:
        """Retrieve analytics by source ID with pagination."""

        result = await db.execute(
            select(self.model)
            .where(self.model.source_id == source_id)
            .order_by(desc(self.model.analysis_date))
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_latest_by_source_id(self, db: AsyncSession, source_id: Mapped[int]) -> Optional[AIAnalytics]:
        """Get latest analytics for a source."""

        result = await db.execute(
            select(self.model)
            .where(self.model.source_id == source_id)
            .order_by(desc(self.model.analysis_date))
            .limit(1)
        )
        return result.scalars().first()

    async def get_by_date_range(
        self,
        db: AsyncSession,
        source_id: Mapped[int],
        start_date: Mapped[date],
        end_date: Mapped[date],
        period_type: str = "daily",
    ) -> Sequence[AIAnalytics]:
        """
        Retrieve analytics for a source within date range.

        Args:
                db: Database session
                source_id: ID of the source
                start_date: Start date of the period
                end_date: End date of the period
                period_type: Type of period ('daily', 'weekly')

        Returns:
                List of AIAnalytics objects
        """
        result = await db.execute(
            select(self.model)
            .where(
                and_(
                    self.model.source_id == source_id,
                    self.model.analysis_date >= start_date,
                    self.model.analysis_date <= end_date,
                    self.model.period_type == period_type,
                )
            )
            .order_by(self.model.analysis_date)
        )
        return result.scalars().all()

    async def get_daily_summary(self, db: AsyncSession, analysis_date: date) -> Sequence[AIAnalytics]:
        """Get all daily summaries for a specific date."""

        result = await db.execute(
            select(self.model)
            .where(and_(self.model.analysis_date == analysis_date, self.model.period_type == "daily"))
            .order_by(self.model.source_id)
        )
        return result.scalars().all()

    async def save_analysis(
        self,
        db: AsyncSession,
        source_id: int,
        analysis_date: date,
        summary_data: dict,
        period_type: str = "daily",
        topic_chain_id: Optional[str] = None,
        parent_analysis_id: Optional[int] = None,
        llm_model: Optional[str] = None,
        prompt_text: Optional[str] = None,
        response_payload: Optional[dict] = None,
    ) -> Row[Any] | RowMapping | Any:
        """
        Save AI analysis results with full LLM tracing.

        Args:
                db: Database session
                source_id: ID of the source
                analysis_date: Date of analysis
                summary_data: AI analysis results in JSON format
                period_type: Type of period ('daily', 'weekly')
                topic_chain_id: Chain ID for ongoing topics
                parent_analysis_id: Parent analysis ID for threaded analysis
                llm_model: LLM model used
                prompt_text: Prompt sent to LLM
                response_payload: Full LLM response payload

        Returns:
                Created AIAnalytics object
        """
        # Check if analysis already exists for this date and source
        existing = await db.execute(
            select(self.model).where(
                and_(
                    self.model.source_id == source_id,
                    self.model.analysis_date == analysis_date,
                    self.model.period_type == period_type,
                )
            )
        )
        existing_analysis = existing.scalars().first()

        if existing_analysis:
            # Update existing analysis
            existing_analysis.summary_data = summary_data
            if topic_chain_id:
                existing_analysis.topic_chain_id = topic_chain_id
            if parent_analysis_id:
                existing_analysis.parent_analysis_id = parent_analysis_id
            if llm_model:
                existing_analysis.llm_model = llm_model
            if prompt_text:
                existing_analysis.prompt_text = prompt_text
            if response_payload:
                existing_analysis.response_payload = response_payload
            await db.commit()
            await db.refresh(existing_analysis)
            return existing_analysis
        else:
            # Create new analysis
            analysis = self.model(
                source_id=source_id,
                analysis_date=analysis_date,
                period_type=period_type,
                summary_data=summary_data,
                topic_chain_id=topic_chain_id,
                parent_analysis_id=parent_analysis_id,
                llm_model=llm_model,
                prompt_text=prompt_text,
                response_payload=response_payload,
            )
            db.add(analysis)
            await db.commit()
            await db.refresh(analysis)
            return analysis

    async def get_trends(self, source_id: Mapped[int], days: int = 30) -> dict:
        """
        Get trends analysis for a source over time.

        Args:
                source_id: ID of the source
                days: Amount days to analyze

        Returns:
                Dictionary with trend analysis
        """
        end_date: Mapped[date] = date.today()
        start_date: Mapped[date] = end_date - timedelta(days=days)

        analytics = await self.filter_by_date_range(source_id, start_date, end_date)

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

    async def get_by_topic_chain(self, db: AsyncSession, topic_chain_id: str) -> Sequence[AIAnalytics]:
        """
        Get all analytics in a topic chain.

        Args:
                db: Database session
                topic_chain_id: Chain ID to filter by

        Returns:
                List of AIAnalytics objects in the chain
        """
        result = await db.execute(
            select(self.model).where(self.model.topic_chain_id == topic_chain_id).order_by(self.model.analysis_date)
        )
        return result.scalars().all()

    async def get_children(self, db: AsyncSession, parent_id: int) -> Sequence[AIAnalytics]:
        """
        Get child analytics for a parent analysis.

        Args:
                db: Database session
                parent_id: Parent analysis ID

        Returns:
                List of child AIAnalytics objects
        """
        result = await db.execute(
            select(self.model).where(self.model.parent_analysis_id == parent_id).order_by(self.model.analysis_date)
        )
        return result.scalars().all()

    @staticmethod
    async def get_sources_without_recent_analysis(db: AsyncSession, days: int = 1) -> Sequence[int]:
        """
        Get source IDs that haven't been analyzed in the specified days.

        Args:
                db: Database session
                days: Amount days to check back

        Returns:
                List of source IDs that need analysis
        """
        cutoff_date: Mapped[date] = date.today() - timedelta(days=days)

        # Subquery to find sources with recent analysis
        from ..source import Source

        subquery = select(AIAnalytics.source_id).where(AIAnalytics.analysis_date >= cutoff_date).subquery()

        # Find sources without recent analysis
        result = await db.execute(select(Source.id).where(~Source.id.in_(select(subquery))).where(Source.is_active))

        return result.scalars().all()
