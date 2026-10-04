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


async def broadcast_digest(
    text: str, channel_filter: str | None = None, tenant_id: int | None = None
) -> dict[str, dict]:
    """
    Publish digest text to configured digest targets.

    Two target sets, additive:
    - env-configured targets: TELEGRAM_DIGEST_CHANNEL_ID / MAX_CHANNEL_ID
    - tenant channels with `is_digest_target=True` (hybrid digest step 3).

    Returns {channel: send_result} — keys are channel names for the env targets
    and "channel:chat_id" for tenant-bound ones.
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

    # Tenant digest targets: every bound chat with is_digest_target=True gets
    # the same payload. The tenant is resolved from the ambient scope when not
    # passed explicitly — the digest job runs inside the job's workspace scope,
    # so this never leaks across workspaces.
    if tenant_id is None:
        from app.core.tenant_context import current_tenant_id

        tenant_id = current_tenant_id()
    if tenant_id is not None:
        from app.models.managers.tenant_manager import tenant_channels

        channels = await tenant_channels.digest_targets(tenant_id)
        for tc in channels:
            key = f"{tc.channel}:{tc.chat_id}"
            ch = get_channel(tc.channel)
            if not ch:
                results[key] = {"success": False, "error": "channel not configured"}
                continue
            results[key] = await ch.send(tc.chat_id, text, parse_mode="HTML")

    return results
