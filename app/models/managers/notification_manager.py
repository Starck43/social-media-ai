from __future__ import annotations

from typing import Optional, TYPE_CHECKING
from datetime import datetime, timedelta, UTC

from .base_manager import BaseManager

if TYPE_CHECKING:
	from ..notification import Notification
	from app.types import NotificationType


class NotificationManager(BaseManager):
	"""
	Manager for Notification model with specialized notification operations.

	Provides methods for:
	— Managing read/unread notifications
	— Filtering by type and related entities
	— Bulk operations on notifications

	Note: By default, returns all notifications (read and unread).
	"""

	def __init__(self):
		from ..notification import Notification
		super().__init__(Notification)

	async def get_unread(
			self,
			notification_type: Optional['NotificationType'] = None
	) -> list['Notification']:
		"""
		Get all unread notifications with optional type filter.

		Args:
			notification_type: An optional filter by notification type

		Returns:
			List of unread Notification objects
		"""
		qs = self.filter(is_read=False)

		if notification_type:
			qs = qs.filter(notification_type=notification_type)

		return await qs.order_by(self.model.created_at.desc())

	async def get_unread_count(
			self,
			notification_type: Optional['NotificationType'] = None
	) -> int:
		"""
		Count unread notifications.

		Args:
			notification_type: An optional filter by notification type

		Returns:
			Amount unread notifications
		"""
		qs = self.filter(is_read=False)

		if notification_type:
			qs = qs.filter(notification_type=notification_type)

		return await qs.count()

	async def mark_as_read(
			self,
			notification_id: int
	) -> Optional['Notification']:
		"""
		Mark a specific notification as read.

		Args:
			notification_id: ID of the notification

		Returns:
			Updated Notification object or None if not found
		"""
		return await self.update_by_id(notification_id, is_read=True)

	async def mark_all_as_read(
			self,
			notification_type: Optional['NotificationType'] = None
	) -> int:
		"""
		Mark all unread notifications as read.

		Args:
			notification_type: An optional filter by notification type

		Returns:
			Amount notifications marked as read
		"""
		unread = await self.get_unread(notification_type=notification_type)

		count = 0
		for notification in unread:
			result = await self.update_by_id(notification.id, is_read=True)
			if result:
				count += 1

		return count

	async def get_by_type(
			self,
			notification_type: 'NotificationType',
			is_read: Optional[bool] = None
	) -> list['Notification']:
		"""
		Get notifications by type.

		Args:
			notification_type: Type of notifications to retrieve
			is_read: An optional filter by read status

		Returns:
			List of Notification objects
		"""
		qs = self.filter(notification_type=notification_type)

		if is_read is not None:
			qs = qs.filter(is_read=is_read)

		return await qs.order_by(self.model.created_at.desc())

	async def get_by_entity(
			self,
			entity_type: str,
			entity_id: int,
			is_read: Optional[bool] = None
	) -> list['Notification']:
		"""
		Get notifications for a specific related entity.

		Args:
			entity_type: Type of entity ('source', 'platform', 'analysis')
			entity_id: ID of the entity
			is_read: An optional filter by read status

		Returns:
			List of Notification objects
		"""
		qs = self.filter(
			related_entity_type=entity_type,
			related_entity_id=entity_id
		)

		if is_read is not None:
			qs = qs.filter(is_read=is_read)

		return await qs.order_by(self.model.created_at.desc())

	async def get_recent(
			self,
			days: int = 7,
			is_read: Optional[bool] = None
	) -> list['Notification']:
		"""
		Get recent notifications from the last N days.

		Args:
			days: Amount days to look back
			is_read: An optional filter by read status

		Returns:
			List of recent Notification objects
		"""
		cutoff_date = datetime.now(UTC) - timedelta(days=days)

		qs = self.filter()

		if is_read is not None:
			qs = qs.filter(is_read=is_read)

		# Filter by date in memory (can be optimized with database filter)
		notifications = await qs.order_by(self.model.created_at.desc())

		return [
			n for n in notifications
			if n.created_at and n.created_at >= cutoff_date
		]

	async def create_notification(
			self,
			title: str,
			message: str,
			notification_type: 'NotificationType',
			related_entity_type: Optional[str] = None,
			related_entity_id: Optional[int] = None
	) -> 'Notification':
		"""
		Create a new notification.

		Args:
			title: Notification title
			message: Notification message
			notification_type: Type of notification
			related_entity_type: Optional related entity type
			related_entity_id: Optional related entity ID

		Returns:
			Created Notification object
		"""
		return await self.create(
			title=title,
			message=message,
			notification_type=notification_type,
			related_entity_type=related_entity_type,
			related_entity_id=related_entity_id,
			is_read=False
		)

	async def delete_old_notifications(
			self,
			days: int = 30
	) -> int:
		"""
		Delete notifications older than specified days.

		Args:
			days: Delete notifications older than this many days

		Returns:
			Amount deleted notifications
		"""
		cutoff_date = datetime.now(UTC) - timedelta(days=days)

		notifications = await self.filter()

		count = 0
		for notification in notifications:
			if notification.created_at and notification.created_at < cutoff_date:
				result = await self.delete_by_id(notification.id)
				if result:
					count += 1

		return count

	async def get_stats(self) -> dict:
		"""
		Get statistics about notifications.

		Returns:
			Dict with notification statistics
		"""
		all_notifications = await self.filter()

		stats = {
			'total': len(all_notifications),
			'unread': len([n for n in all_notifications if not n.is_read]),
			'read': len([n for n in all_notifications if n.is_read]),
			'by_type': {}
		}

		for notification in all_notifications:
			type_name = (
				notification.notification_type.value
				if hasattr(notification.notification_type, 'value')
				else str(notification.notification_type)
			)

			if type_name not in stats['by_type']:
				stats['by_type'][type_name] = {'total': 0, 'unread': 0}

			stats['by_type'][type_name]['total'] += 1
			if not notification.is_read:
				stats['by_type'][type_name]['unread'] += 1

		return stats
