from __future__ import annotations

from typing import Optional, Sequence, TYPE_CHECKING

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

from .base_manager import BaseManager

if TYPE_CHECKING:
    from .. import Notification
else:
    # Use string literal to avoid circular import
    Notification = 'Notification'


class NotificationManager(BaseManager):
    """Manager for Notification model operations."""

    def __init__(self):
        # Use string literal to avoid circular import
        from ..notification import Notification as N
        super().__init__(N)

    async def get_user_notifications(
        self,
        db: AsyncSession,
        user_id: Mapped[int],
        is_read: Mapped[bool] | None = None,
        skip: int = 0,
        limit: int = 100
    ) -> Sequence[Notification]:
        """Retrieve notifications for a specific user with optional read status filter."""

        query = select(self.model).where(self.model.user_id == user_id)

        if is_read is not None:
            query = query.where(self.model.is_read == is_read)

        result = await db.execute(
            query.order_by(self.model.created_at.desc())
                 .offset(skip)
                 .limit(limit)
        )
        return result.scalars().all()

    async def get_unread_count(
        self,
        db: AsyncSession,
        user_id: Mapped[int]
    ) -> int:
        """Count unread notifications for a user."""

        result = await db.execute(
            select(self.model)
            .where(
                (self.model.user_id == user_id) &
                (not self.model.is_read)
            )
        )
        return len(result.scalars().all())

    async def mark_as_read(
        self,
        db: AsyncSession,
        notification_id: Mapped[int],
        user_id: Mapped[int]
    ) -> Optional[Notification]:
        """Mark a specific notification as read."""

        notification = self.get(id=notification_id)

        if notification and notification.user_id == user_id:
            notification.is_read = True
            await db.commit()
            await db.refresh(notification)
            return notification
        return None

    async def mark_all_as_read(
        self,
        db: AsyncSession,
        user_id: Mapped[int]
    ) -> int:
        """Mark all notifications for a user as read."""

        result = await db.execute(
            select(self.model)
            .where(
                (self.model.user_id == user_id) &
                (not self.model.is_read)
            )
        )
        notifications = result.scalars().all()

        for notification in notifications:
            notification.is_read = True

        if notifications:
            await db.commit()

        return len(notifications)

    async def get_recent_notifications(
        self, 
        db: AsyncSession, 
        days: int = 7,
        skip: int = 0, 
        limit: int = 100
    ) -> Sequence[Notification]:
        """
        Retrieve recent notifications from the last N days.
        
        Args:
            db: Database session
            days: Amount days to look back
            skip: Amount records to skip
            limit: Maximum amount records to return
            
        Returns:
            List of Notification objects
        """
        from datetime import timedelta
        
        date_threshold = func.now() - timedelta(days=days)
        query = self.filter_by_date_range(
            date_column=self.model.created_at,
            start_date=date_threshold,
            skip=skip,
            limit=limit
        )
        result = await db.execute(query)
        return result.scalars().all()
