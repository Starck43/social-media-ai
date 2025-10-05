from __future__ import annotations

from datetime import timedelta
from typing import Sequence, TYPE_CHECKING

from sqlalchemy import select, Row, RowMapping, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

from .base_manager import BaseManager

if TYPE_CHECKING:
    from .. import Post, Comment
else:
    # Use string literals to avoid circular imports
    Post = 'Post'
    Comment = 'Comment'


class PostManager(BaseManager['Post']):
    """Manager for Post model operations."""

    def __init__(self):
        # Use string literal to avoid circular import
        from ..content import Post as P
        super().__init__(P)
    
    async def get_by_group_id(
        self, 
        db: AsyncSession, 
        group_id: Mapped[int],
        skip: int = 0, 
        limit: int = 100
    ) -> Sequence[Post]:
        """Retrieve posts by group ID with pagination."""

        result = await db.execute(
            select(self.model)
            .where(self.model.group_id == group_id)
            .order_by(self.model.post_date.desc())
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()
    
    async def get_recent_posts(
        self, 
        db: AsyncSession, 
        days: int = 7,
        skip: int = 0, 
        limit: int = 100
    ) -> Sequence[Post]:
        """
        Retrieve recent posts from the last N days.
        
        Args:
            db: Database session
            days: Amount days to look back
            skip: Amount records to skip
            limit: Maximum amount records to return
            
        Returns:
            List of Post objects
        """

        date_threshold = func.now() - timedelta(days=days)
        query = self.filter_by_date_range(
            date_column=self.model.__table__.c.post_date,
            start_date=date_threshold,
            skip=skip,
            limit=limit
        )
        result = await db.execute(query)
        return result.scalars().all()


class CommentManager(BaseManager['Comment']):
    """Manager for Comment model operations."""
    
    def __init__(self):
        # Use string literal to avoid circular import
        from ..content import Comment as C
        super().__init__(C)

    async def get_by_post_id(
        self, 
        db: AsyncSession,
        post_id: Mapped[int],
        skip: int = 0,
        limit: int = 100
    ) -> Sequence[Row[Comment] | RowMapping | Comment]:
        """Retrieve comments by post ID with pagination."""

        result = await db.execute(
            select(self.model)
            .where(self.model.post_id == post_id)
            .order_by(self.model.comment_date.desc())
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()
    
    async def get_by_sentiment(
        self, 
        db: AsyncSession, 
        sentiment_label: Mapped[str],
        skip: int = 0, 
        limit: int = 100
    ) -> Sequence[Comment]:
        """Retrieve comments by sentiment label with pagination."""

        result = await db.execute(
            select(self.model)
            .where(self.model.sentiment_label == sentiment_label)
            .order_by(self.model.comment_date.desc())
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()
    
    async def get_recent_comments(
        self, 
        db: AsyncSession, 
        days: int = 7,
        skip: int = 0, 
        limit: int = 100
    ) -> Sequence[Comment]:
        """
        Retrieve recent comments from the last N days.
        
        Args:
            db: Database session
            days: Amount days to look back
            skip: Amount records to skip
            limit: Maximum amount records to return
            
        Returns:
            List of Comment objects
        """

        date_threshold = func.now() - timedelta(days=days)
        query = self.filter_by_date_range(
            date_column=self.model.__table__.c.post_date,
            start_date=date_threshold,
            skip=skip,
            limit=limit
        )
        result = await db.execute(query)
        return result.scalars().all()
