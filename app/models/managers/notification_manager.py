"""Workspace notification queries.

Every read and write goes through ``BaseManager`` querysets, so the tenant guard
applies to bulk updates, deletes and aggregates as well as to single-row reads.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func

from app.utils.enum_helpers import get_enum_value

from .base_manager import BaseManager, QuerySet

if TYPE_CHECKING:
    from app.types import NotificationType

    from ..notifications.notification import Notification


class NotificationManager(BaseManager["Notification"]):
    """Workspace-wide notification feed.

    ``is_read`` is a shared workspace flag, not a per-user acknowledgement (see
    docs/NOTIFICATIONS.md). Methods return read and unread rows unless a filter
    says otherwise; all filtering, counting and bulk changes run in SQL.
    """

    def __init__(self) -> None:
        from ..notifications.notification import Notification

        super().__init__(Notification)

    def _feed(
        self,
        *,
        notification_type: NotificationType | None = None,
        is_read: bool | None = None,
        **filters: Any,
    ) -> QuerySet[Notification]:
        """Tenant-scoped base queryset with the optional common filters applied."""
        if notification_type is not None:
            filters["notification_type"] = notification_type
        if is_read is not None:
            filters["is_read"] = is_read
        return self.filter(**filters)

    def _newest_first(self, queryset: QuerySet[Notification]) -> QuerySet[Notification]:
        return queryset.order_by(self.model.created_at.desc(), self.model.id.desc())

    async def get_unread(self, notification_type: NotificationType | None = None) -> list[Notification]:
        """Unread notifications, newest first, optionally of one type."""
        return list(await self._newest_first(self._feed(notification_type=notification_type, is_read=False)))

    async def get_unread_count(self, notification_type: NotificationType | None = None) -> int:
        """Number of unread notifications, optionally of one type."""
        return await self._feed(notification_type=notification_type, is_read=False).count()

    async def mark_as_read(self, notification_id: int) -> Notification | None:
        """Set the shared read flag on one notification; None if not visible."""
        return await self.update_by_id(notification_id, is_read=True)

    async def mark_all_as_read(self, notification_type: NotificationType | None = None) -> int:
        """Set the shared read flag on every unread row in one UPDATE; returns the count."""
        return await self._feed(notification_type=notification_type, is_read=False).update(is_read=True)

    async def get_by_type(self, notification_type: NotificationType, is_read: bool | None = None) -> list[Notification]:
        """Notifications of one type, newest first."""
        return list(await self._newest_first(self._feed(notification_type=notification_type, is_read=is_read)))

    async def get_by_entity(self, entity_type: str, entity_id: int, is_read: bool | None = None) -> list[Notification]:
        """Notifications attributed to one related entity, newest first."""
        queryset = self._feed(is_read=is_read, related_entity_type=entity_type, related_entity_id=entity_id)
        return list(await self._newest_first(queryset))

    async def get_recent(self, days: int = 7, is_read: bool | None = None) -> list[Notification]:
        """Notifications created during the last ``days`` days, newest first."""
        cutoff = datetime.now(UTC) - timedelta(days=days)
        return list(await self._newest_first(self._feed(is_read=is_read, created_at__gte=cutoff)))

    async def create_notification(
        self,
        title: str,
        message: str,
        notification_type: NotificationType,
        related_entity_type: str | None = None,
        related_entity_id: int | None = None,
    ) -> Notification:
        """Create one unread notification in the current workspace."""
        return await self.create(
            title=title,
            message=message,
            notification_type=notification_type,
            related_entity_type=related_entity_type,
            related_entity_id=related_entity_id,
            is_read=False,
        )

    async def delete_old_notifications(self, days: int = 30) -> int:
        """Delete notifications older than ``days`` days in one DELETE; returns the count."""
        cutoff = datetime.now(UTC) - timedelta(days=days)
        return await self.filter(created_at__lt=cutoff).delete()

    async def get_stats(self) -> dict[str, Any]:
        """Totals and per-type read/unread counts from one grouped query."""
        rows = await (
            self.filter()
            .values(self.model.notification_type, self.model.is_read, func.count(self.model.id))
            .group_by(self.model.notification_type, self.model.is_read)
            .rows()
        )
        stats: dict[str, Any] = {"total": 0, "unread": 0, "read": 0, "by_type": {}}
        for notification_type, is_read, amount in rows:
            bucket = stats["by_type"].setdefault(get_enum_value(notification_type), {"total": 0, "unread": 0})
            bucket["total"] += amount
            stats["total"] += amount
            if is_read:
                stats["read"] += amount
            else:
                bucket["unread"] += amount
                stats["unread"] += amount
        return stats
