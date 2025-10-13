import logging
from typing import Optional
import httpx
from app.core.config import settings
from app.types.models import NotificationType

logger = logging.getLogger(__name__)


class MessengerService:
    """Service for sending notifications to various messengers."""

    def __init__(self):
        self.telegram_bot_token = settings.TELEGRAM_BOT_TOKEN
        self.vk_app_secret = settings.VK_SERVICE_ACCESS_TOKEN

    async def send_notification(
        self,
        title: str,
        message: str,
        notification_type: NotificationType,
        messenger: str = "all",
        recipient_id: Optional[str] = None,
    ) -> dict:
        """
        Send notification to messenger(s).

        Args:
            title: Notification title
            message: Notification message
            notification_type: Type of notification
            messenger: Which messenger to use ('telegram', 'vk', 'all')
            recipient_id: Optional specific recipient ID

        Returns:
            Dict with sending results
        """
        results = {"telegram": None, "vk": None}

        # Determine if this is a critical notification
        critical_types = [
            NotificationType.API_ERROR,
            NotificationType.CONNECTION_ERROR,
            NotificationType.SYSTEM_BACKUP,
        ]
        is_critical = notification_type in critical_types

        if messenger in ["telegram", "all"]:
            results["telegram"] = await self._send_telegram(
                title, message, is_critical, recipient_id
            )

        if messenger in ["vk", "all"]:
            results["vk"] = await self._send_vk(
                title, message, is_critical, recipient_id
            )

        return results

    async def _send_telegram(
        self,
        title: str,
        message: str,
        is_critical: bool = False,
        chat_id: Optional[str] = None,
    ) -> dict:
        """Send notification via Telegram bot."""
        if not self.telegram_bot_token:
            logger.warning("Telegram bot token not configured, skipping")
            return {"success": False, "error": "Token not configured"}

        # Format message
        icon = "🚨" if is_critical else "ℹ️"
        formatted_message = f"{icon} <b>{title}</b>\n\n{message}"

        # Use environment variable for default chat ID or fallback to admin
        if not chat_id:
            chat_id = getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", None)

        if not chat_id:
            logger.warning("No Telegram chat ID configured")
            return {"success": False, "error": "No chat ID"}

        try:
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    json={
                        "chat_id": chat_id,
                        "text": formatted_message,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                    timeout=10.0,
                )

                if response.status_code == 200:
                    logger.info(f"Telegram notification sent successfully to {chat_id}")
                    return {"success": True, "chat_id": chat_id}
                else:
                    logger.error(
                        f"Telegram API error: {response.status_code} - {response.text}"
                    )
                    return {
                        "success": False,
                        "error": f"API error {response.status_code}",
                    }

        except Exception as e:
            logger.error(f"Error sending Telegram notification: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _send_vk(
        self,
        title: str,
        message: str,
        is_critical: bool = False,
        user_id: Optional[str] = None,
    ) -> dict:
        """Send notification via VK messages."""
        if not self.vk_app_secret:
            logger.warning("VK app secret not configured, skipping")
            return {"success": False, "error": "Secret not configured"}

        # VK implementation would go here
        # This is a placeholder for VK API integration

        logger.info("VK notification sending not yet implemented")
        return {"success": False, "error": "Not implemented"}

    async def send_critical_alert(
        self, title: str, message: str, error_details: Optional[str] = None
    ):
        """
        Send critical alert to all configured messengers.

        Use this for system errors, API failures, etc.
        """
        full_message = message
        if error_details:
            full_message += f"\n\nDetails:\n{error_details}"

        logger.critical(f"Critical alert: {title} - {message}")

        results = await self.send_notification(
            title=f"[CRITICAL] {title}",
            message=full_message,
            notification_type=NotificationType.API_ERROR,
            messenger="all",
        )

        return results

    async def send_report_ready(
        self, report_name: str, report_url: Optional[str] = None
    ):
        """
        Send notification when a report is ready.

        Args:
            report_name: Name of the report
            report_url: Optional URL to access the report
        """
        message = f"Report '{report_name}' is ready for viewing."
        if report_url:
            message += f"\n\nAccess here: {report_url}"

        logger.info(f"Sending report ready notification: {report_name}")

        return await self.send_notification(
            title="Report Ready",
            message=message,
            notification_type=NotificationType.REPORT_READY,
            messenger="telegram",  # Only Telegram for non-critical
        )

    async def send_trend_alert(
        self,
        source_name: str,
        trend_description: str,
        sentiment: str = "neutral",
    ):
        """
        Send notification about detected trends.

        Args:
            source_name: Name of the source where trend was detected
            trend_description: Description of the trend
            sentiment: Sentiment of the trend (positive/negative/neutral)
        """
        sentiment_icon = {"positive": "📈", "negative": "📉", "neutral": "➡️"}.get(
            sentiment, "ℹ️"
        )

        message = f"{sentiment_icon} Trend detected in {source_name}:\n\n{trend_description}"

        logger.info(f"Sending trend alert for {source_name}")

        return await self.send_notification(
            title="Trend Alert",
            message=message,
            notification_type=NotificationType.TREND_ALERT,
            messenger="telegram",
        )


# Convenience singleton
messenger_service = MessengerService()
