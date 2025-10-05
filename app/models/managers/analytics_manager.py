from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence, TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..analytics import Statistics, AIAnalysisResult
else:
    # Use string literals to avoid circular imports
    Statistics = 'Statistics'
    AIAnalysisResult = 'AIAnalysisResult'


class StatisticsManager(BaseManager['Statistics']):
    """Manager for Statistics model operations."""

    def __init__(self):
        # Use string literal to avoid circular import
        from ..analytics import Statistics as S
        super().__init__(S)

    async def get_by_group_id(
        self, 
        db: AsyncSession,
        group_id: Mapped[int],
        skip: int = 0, 
        limit: int = 100
    ) -> Sequence[Statistics]:
        """Retrieve statistics by group ID with pagination."""

        result = await db.execute(
            select(self.model)
            .where(self.model.group_id == group_id)
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()
    
    async def get_latest_by_group_id(
        self, 
        db: AsyncSession, 
        group_id: Mapped[int]
    ) -> Optional[Statistics]:
        """Get latest statistics for a dashboard."""

        result = await db.execute(
            select(self.model)
            .where(self.model.group_id == group_id)
            .order_by(self.model.date.desc())
            .limit(1)
        )
        return result.scalars().first()


class AIAnalysisResultManager(BaseManager['AIAnalysisResult']):
    """Manager for AI analysis results."""
    
    def __init__(self):
        # Use string literal to avoid circular import
        from ..analytics import AIAnalysisResult as A
        super().__init__(A)

    async def get_by_group_and_type(
        self, 
        db: AsyncSession, 
        group_id: Mapped[int],
        analysis_type: str,
        skip: int = 0,
        limit: int = 100
    ) -> Sequence[AIAnalysisResult]:
        """
        Retrieve analysis results by group ID and analysis type with pagination.
        
        Args:
            db: Database session
            group_id: ID of the group
            analysis_type: Type of analysis to filter by
            skip: Amount records to skip
            limit: Maximum amount records to return
            
        Returns:
            List of AIAnalysisResult objects
        """
        query = self.filter_by_date_range(
            date_column=self.model.__table__.c.period_start,
            skip=skip,
            limit=limit
        ).filter(
            (self.model.group_id == group_id) &
            (self.model.analysis_type == analysis_type)
        )
        result = await db.execute(query)
        return result.scalars().all()
    
    async def get_latest_by_group_and_type(
        self, 
        db: AsyncSession, 
        group_id: Mapped[int],
        analysis_type: str
    ) -> Optional[AIAnalysisResult]:
        """
        Retrieve the most recent analysis result for a group and analysis type.
        
        Args:
            db: Database session
            group_id: ID of the group
            analysis_type: Type of analysis to filter by
            
        Returns:
            AIAnalysisResult object if found, None otherwise
        """
        query = self.filter_by_date_range(
            date_column=self.model.__table__.c.period_start,
            limit=1
        ).filter(
            (self.model.group_id == group_id) &
            (self.model.analysis_type == analysis_type)
        )
        result = await db.execute(query)
        return result.scalars().first()

    async def save_sentiment_analysis(self, group_id: int, analysis_data: dict):
        """Save sentiment analysis from DeepSeek."""

        return self.create(
            group_id=group_id,
            analysis_type='sentiment',
            results=analysis_data,
            period_start=datetime.now(),
            period_end=datetime.now()
        )

    async def get_trends(self, db: AsyncSession, group_id: int, days: int = 30):
        """Get trends for period."""
        # Анализ изменения метрик over time
        pass
