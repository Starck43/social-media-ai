"""Explicit workspace delivery and fixed-template operator alerts.

Workspace messages never use an environment recipient. Operator alerts accept
only a catalog key, not report text, source names, URLs or exception details.
"""

from __future__ import annotations

import logging
from html import escape

import httpx

from app.core.config import settings
from app.core.tenant_context import current_tenant_id, is_bypass
from app.types import NotificationType

logger = logging.getLogger(__name__)

OPERATOR_ALERTS = {
    "collection_failed": ("Collection failure", "A collection operation failed. Check restricted operational logs."),
    "api_error": ("API failure", "An integration operation failed. Check restricted operational logs."),
    "connection_error": ("Connection failure", "An integration connection failed. Check restricted operational logs."),
    "backup_failed": ("Backup failure", "A backup operation failed. Check the backup service."),
    "backup_completed": ("Backup completed", "A backup operation completed. Verify the scheduled restore checks."),
}


class MessengerService:
    """Deliver workspace messages only to an explicitly authorized binding."""

    async def send_notification(
        self,
        title: str,
        message: str,
        notification_type: NotificationType,
        messenger: str = "all",
        recipient_id: str | None = None,
        *,
        tenant_id: int | None = None,
    ) -> dict:
        """Require workspace scope and an explicit active owned recipient.

        A trusted operator may choose a workspace explicitly; an unscoped
        operator never defaults to bootstrap. All denied routes fail before HTTP.
        """
        from app.models.managers.tenant_manager import tenant_channels, tenants

        if messenger not in {"telegram", "vk", "all"}:
            raise ValueError("Unsupported notification messenger")
        selected_channels = ("telegram", "vk") if messenger == "all" else (messenger,)
        results = {"telegram": None, "vk": None}

        def denied(error: str) -> dict:
            for channel in selected_channels:
                results[channel] = {"success": False, "error": error}
            return results

        recipient = str(recipient_id).strip() if recipient_id is not None else ""
        if not recipient:
            return denied("Explicit workspace recipient required")
        ambient = current_tenant_id()
        workspace_id = tenant_id if tenant_id is not None else ambient
        if not is_bypass() and (ambient is None or workspace_id != ambient):
            return denied("Current workspace required; foreign overrides forbidden")
        if workspace_id is None:
            return denied("Explicit workspace required for operator delivery")
        tenant = await tenants.get(id=workspace_id)
        if tenant is None or not tenant.is_active:
            return denied("Workspace unavailable")

        for channel in selected_channels:
            binding = await tenant_channels.get_binding(channel=channel, chat_id=recipient)
            if (
                binding is None
                or binding.tenant_id != workspace_id
                or not binding.is_active
                or binding.channel != channel
                or str(binding.chat_id) != recipient
            ):
                results[channel] = {"success": False, "error": "Recipient unavailable for workspace"}
                continue
            if channel == "vk":
                # The existing notification transport is a stub, not an enabled feature.
                results[channel] = {"success": False, "error": "VK notifications not implemented"}
                continue
            results[channel] = await self._send_telegram(
                title,
                message,
                is_critical=notification_type
                in {NotificationType.API_ERROR, NotificationType.CONNECTION_ERROR, NotificationType.SYSTEM_BACKUP},
                chat_id=binding.chat_id,
            )
        return results

    async def _send_telegram(self, title: str, message: str, *, is_critical: bool, chat_id: str) -> dict:
        """Low-level transport, with no fallback and no raw transport diagnostics."""
        if not chat_id or not str(chat_id).strip():
            return {"success": False, "error": "Explicit chat ID required"}
        token = settings.TELEGRAM_BOT_TOKEN
        if not token:
            return {"success": False, "error": "Telegram token not configured"}
        icon = "🚨" if is_critical else "ℹ️"
        formatted = f"{icon} <b>{escape(title)}</b>\n\n{escape(message)}"
        if len(formatted) > 4096:
            return {"success": False, "error": "Notification exceeds transport limit"}
        try:
            url = f"{settings.TELEGRAM_API_BASE_URL.rstrip('/')}/bot{token}/sendMessage"
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    json={
                        "chat_id": str(chat_id),
                        "text": formatted,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                    timeout=10.0,
                )
            if response.status_code != 200 or response.json().get("ok") is not True:
                logger.warning("Telegram rejected notification delivery (HTTP %s)", response.status_code)
                return {"success": False, "error": "Telegram rejected notification"}
            return {"success": True, "chat_id": str(chat_id)}
        except Exception:
            # HTTP exceptions can contain a token-bearing URL or echoed customer text.
            logger.warning("Telegram notification transport failed")
            return {"success": False, "error": "Notification transport failure"}

    async def send_operator_alert(self, event_code: str) -> dict:
        """Use only catalog text and the explicitly configured operator chat.

        The caller may be a scoped worker. No free-form details or tenant data
        cross this boundary; operator chat configuration is deployment-trusted.
        """
        template = OPERATOR_ALERTS.get(event_code)
        if template is None:
            return {"success": False, "error": "Unknown operator alert code"}
        chat_id = settings.TELEGRAM_ADMIN_CHAT_ID
        if not chat_id or not str(chat_id).strip():
            return {"success": False, "error": "Operator chat not configured"}
        title, message = template
        return await self._send_telegram(
            title, message, is_critical=event_code != "backup_completed", chat_id=str(chat_id).strip()
        )

    async def send_critical_alert(self, title: str, message: str, error_details: str | None = None) -> dict:
        """Legacy shim: deliberately discard all free-form content.

        New callers should specify a catalog event with send_operator_alert.
        Keeping the old signature does not grant permission to forward its text.
        """
        return {"telegram": await self.send_operator_alert("api_error"), "vk": None}

    async def send_report_ready(
        self,
        report_name: str,
        report_url: str | None = None,
        *,
        recipient_id: str | None = None,
        tenant_id: int | None = None,
    ) -> dict:
        """Report text is workspace-only; missing recipient fails closed."""
        message = f"Report '{report_name}' is ready for viewing."
        if report_url:
            message += f"\n\nAccess here: {report_url}"
        return await self.send_notification(
            "Report Ready",
            message,
            NotificationType.REPORT_READY,
            messenger="telegram",
            recipient_id=recipient_id,
            tenant_id=tenant_id,
        )

    async def send_trend_alert(
        self,
        source_name: str,
        trend_description: str,
        sentiment: str = "neutral",
        *,
        recipient_id: str | None = None,
        tenant_id: int | None = None,
    ) -> dict:
        """Trend text is workspace-only; missing recipient fails closed."""
        icon = {"positive": "📈", "negative": "📉", "neutral": "➡️"}.get(sentiment, "ℹ️")
        return await self.send_notification(
            "Trend Alert",
            f"{icon} Trend detected in {source_name}:\n\n{trend_description}",
            NotificationType.TREND_ALERT,
            messenger="telegram",
            recipient_id=recipient_id,
            tenant_id=tenant_id,
        )


messenger_service = MessengerService()
