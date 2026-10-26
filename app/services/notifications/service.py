import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core.permissions import get_current_user
from app.core.tenant_context import current_tenant_id
from app.models import Notification, NotificationReceipt
from app.types import NotificationType

logger = logging.getLogger(__name__)

# Try to import messenger service, but don't fail if not available
try:
    from .messenger import messenger_service

    MESSENGER_AVAILABLE = True
except ImportError:
    MESSENGER_AVAILABLE = False
    logger.warning("Messenger service not available")


class NotificationService:
    """DB-first workspace-origin events with explicit global User inbox receipts.

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
        recipient_id: Optional[str] = None,
        recipient_user_ids: Optional[list[int]] = None,
        severity: str = "info",
        dedup_key: Optional[str] = None,
        expires_at=None,
    ) -> Notification:
        """
        Create a notification.

        Args:
            title: Notification title
            message: Notification message
            ntype: Notification type
            entity_type: Related entity type
            entity_id: Related entity ID
            send_to_messenger: If True, attempt explicitly addressed workspace delivery
            recipient_id: Explicit legacy workspace Telegram binding; not inbox ownership
            recipient_user_ids: Global User IDs, never TenantUser IDs. None derives only
                the current actor with active origin membership; [] is archive-only.
            severity: info, warning or action_required
            dedup_key: Immutable logical event key, unique within its origin workspace
            expires_at: Timezone-aware expiry; default 30 days

        Returns:
            Created Notification object
        """
        logger.info("Creating notification (type=%s, messenger_requested=%s)", ntype, send_to_messenger)

        actor = get_current_user()
        recipients = recipient_user_ids
        if recipients is None:
            from app.models import TenantUser

            membership = (
                (
                    await TenantUser.objects.filter(
                        user_id=actor.id,
                        tenant_id=current_tenant_id(),
                        channel="web",
                        is_active=True,
                        role_id__isnull=False,
                    ).first()
                )
                if actor and actor.is_active
                else None
            )
            recipients = [actor.id] if membership is not None else []
        notification = await NotificationReceipt.objects.publish(
            recipient_user_ids=recipients,
            severity=severity,
            dedup_key=dedup_key,
            expires_at=expires_at or datetime.now(timezone.utc) + timedelta(days=30),
            title=title,
            message=message,
            notification_type=ntype,
            related_entity_type=entity_type,
            related_entity_id=entity_id,
        )

        # Send to messenger if requested and available
        if send_to_messenger and MESSENGER_AVAILABLE and notification.tenant_id is not None:
            try:
                result = await messenger_service.send_notification(
                    title=title,
                    message=message,
                    notification_type=ntype,
                    messenger="telegram",
                    recipient_id=recipient_id,
                    tenant_id=notification.tenant_id,
                )
                if (result.get("telegram") or {}).get("success") is True:
                    logger.info("Notification %s delivered to messenger", notification.id)
                else:
                    logger.warning("Notification %s not delivered to messenger", notification.id)
            except Exception:
                logger.warning("Notification messenger delivery failed")

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
