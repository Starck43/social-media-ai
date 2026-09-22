"""Channel registry: enabled channels by config + digest broadcast."""
from __future__ import annotations

import logging

from app.channels.max import MaxChannel
from app.channels.telegram import TelegramChannel
from app.core.config import settings

logger = logging.getLogger(__name__)


def get_channel(name: str):
    """Return an enabled channel instance or None if not configured."""
    if name == "telegram":
        ch = TelegramChannel()
        return ch if ch.enabled else None
    if name == "max":
        ch = MaxChannel()
        return ch if ch.enabled else None
    return None


def enabled_channels() -> list:
    """All channels with credentials configured."""
    channels = []
    for name in ("telegram", "max"):
        ch = get_channel(name)
        if ch:
            channels.append(ch)
    return channels


async def broadcast_digest(text: str, channel_filter: str | None = None) -> dict[str, dict]:
    """
    Publish digest text to configured digest targets:
    - Telegram: TELEGRAM_DIGEST_CHANNEL_ID (if token+id set)
    - MAX: MAX_CHANNEL_ID (if token+id set)

    Returns {channel: send_result}.
    """
    results: dict[str, dict] = {}
    targets = [("telegram", settings.TELEGRAM_DIGEST_CHANNEL_ID), ("max", settings.MAX_CHANNEL_ID)]
    for name, chat_id in targets:
        if channel_filter and name != channel_filter:
            continue
        if not chat_id:
            continue
        ch = get_channel(name)
        if not ch:
            results[name] = {"success": False, "error": "channel not configured"}
            continue
        results[name] = await ch.send(chat_id, text, parse_mode="HTML")
    return results
