import logging
from typing import Optional

from app.models import Notification
from app.types.models import NotificationType

logger = logging.getLogger(__name__)

# Try to import messenger service, but don't fail if not available
try:
    from .messenger import messenger_service

    MESSENGER_AVAILABLE = True
except ImportError:
    MESSENGER_AVAILABLE = False
    logger.warning("Messenger service not available")


class NotificationService:
    """High-level notification service built on top of Notification.objects.

    This service intentionally keeps delivery channels simple (DB-only) and
    provides helpers for common events. Extend with email/webhook if needed.
    """

    async def create(
        self,
        title: str,
        message: str,
        ntype: NotificationType,
        *,
        entity_type: Optional[str] = None,
        entity_id: Optional[int] = None,
        send_to_messenger: bool = False,
    ) -> Notification:
        """
        Create a notification.

        Args:
            title: Notification title
            message: Notification message
            ntype: Notification type
            entity_type: Related entity type
            entity_id: Related entity ID
            send_to_messenger: If True, also send to messenger (Telegram)

        Returns:
            Created Notification object
        """
        logger.info(f"Creating notification: {title} (type={ntype}, send_to_messenger={send_to_messenger})")

        notification = await Notification.objects.create_notification(
            title=title,
            message=message,
            notification_type=ntype,
            related_entity_type=entity_type,
            related_entity_id=entity_id,
        )

        # Send to messenger if requested and available
        if send_to_messenger and MESSENGER_AVAILABLE:
            try:
                await messenger_service.send_notification(
                    title=title, message=message, notification_type=ntype, messenger="telegram"
                )
                logger.info(f"Notification {notification.id} sent to messenger")
            except Exception as e:
                logger.error(f"Failed to send notification to messenger: {e}")

        return notification

    async def report_ready(self, entity_type: str, entity_id: int, title: str, message: str) -> Notification:
        return await self.create(
            title, message, NotificationType.REPORT_READY, entity_type=entity_type, entity_id=entity_id
        )

    async def api_error(
        self, title: str, message: str, *, entity_type: Optional[str] = None, entity_id: Optional[int] = None
    ) -> Notification:
        return await self.create(
            title, message, NotificationType.API_ERROR, entity_type=entity_type, entity_id=entity_id
        )

    async def rate_limit_warning(self, platform_name: str, remaining: int) -> Notification:
        title = f"Rate limit warning for {platform_name}"
        message = f"Remaining requests: {remaining}"
        return await self.create(
            title, message, NotificationType.RATE_LIMIT_WARNING, entity_type="platform", entity_id=None
        )

    async def keyword_mention(self, source_id: int, keyword: str, context: str) -> Notification:
        title = f"Keyword mention: {keyword}"
        message = context[:1000]
        return await self.create(
            title, message, NotificationType.KEYWORD_MENTION, entity_type="source", entity_id=source_id
        )


# Convenience singleton-like helper
notify = NotificationService()
