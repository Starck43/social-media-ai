"""Push-based ingest: channel updates the bot sees become analyzed content.

Telegram's Bot API cannot read a channel's or a chat's history — a bot only
receives what arrives through `getUpdates` while it is running. So Telegram
cannot follow the VK path (`wall.get` on demand). Instead, the channel listener,
which already consumes the bot's updates, fans channel posts out to this module.

Two consequences worth remembering:

- Only future posts are collected; there is no backfill. Historical depth needs
  a user session (MTProto), which is a separate decision.
- `getUpdates` acknowledges nothing until the offset advances, and the offset
  lives in memory, so a restart replays up to 24h of updates. `Source.last_item_id`
  is therefore the per-source watermark: ids at or below it are skipped, so a
  replay never pays for the same post twice.

Analysis reuses `AIAnalyzer.base_analyze_content`, the same entry point jobs use,
so an ingested Telegram post lands in `ai_analytics` exactly like a collected VK
post. The listener already runs inside the owning workspace's tenant scope.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.permissions import service_permission_scope

logger = logging.getLogger(__name__)


def normalize_channel_post(inbound: Any) -> Optional[dict[str, Any]]:
    """Map one Telegram update to the shared normalized content-item contract.

    Keys mirror `VKClient._normalize_response` / `TelegramClient._normalize_response`
    so the analyzer treats the item identically.
    """
    raw = getattr(inbound, "raw", None) or {}
    post = raw.get("channel_post") or raw.get("message") or {}
    text = post.get("text")
    if not text:
        return None

    published = post.get("date")
    published_ts = float(published) if isinstance(published, (int, float, str)) else None

    return {
        "id": str(post.get("message_id", "")),
        "external_id": f"{inbound.chat_id}_{post.get('message_id', '')}",
        "text": text,
        "date": datetime.fromtimestamp(published_ts, tz=timezone.utc) if published_ts else datetime.now(timezone.utc),
        "views": post.get("views", 0) or 0,
        "forwards": post.get("forward_count", 0) or 0,
        "reactions": 0,
        "metric_availability": {
            "reactions": False,
            "views": post.get("views") is not None,
            "comments": post.get("reply_count") is not None,
        },
        "comments": post.get("reply_count", 0) or 0,
        "source_type": "channel",
        "platform": "telegram",
        "message_type": "post",
    }


def _is_watermark_passed(item: dict[str, Any], watermark: str | None) -> bool:
    """True when the item is older than the source's high-water mark."""
    if not watermark:
        return False
    try:
        return int(item["id"]) <= int(watermark)
    except (TypeError, ValueError):
        return False


async def _find_source(chat_id: str) -> Optional[tuple[int, int, Optional[str]]]:
    """Locate the monitored source for a chat, across workspaces.

    Deliberately reads under `bypass`: a channel post arrives with no workspace
    context at all, so the owning workspace must be discovered before anything
    tenant-scoped can be touched (the job dispatcher works the same way).
    Returns `(source_id, tenant_id, last_item_id)` or None.
    """
    from app.core.tenant_context import tenant_scope
    from app.models import Platform, Source

    with tenant_scope(bypass=True):
        # Platform rows are named per deployment ("Telegram", "Телеграм", ...), so
        # the type column is the stable key — never the display name.
        platform = await Platform.objects.filter(platform_type="telegram").first()
        if platform is None:
            return None
        source = await Source.objects.filter(platform_id=platform.id, external_id=str(chat_id), is_active=True).first()
        if source is None:
            return None
        return source.id, source.tenant_id, getattr(source, "last_item_id", None)


async def _is_digest_target(tenant_id: int, channel: str, chat_id: str) -> bool:
    """True when the chat is the workspace's digest delivery channel.

    Reads under `bypass` for the same reason as `_find_source`: the update
    arrives with no workspace context, and by this point the source's tenant
    is already known.
    """
    from app.core.tenant_context import tenant_scope
    from app.models import TenantChannel

    with tenant_scope(bypass=True):
        binding = await TenantChannel.objects.filter(
            tenant_id=tenant_id,
            channel=channel,
            chat_id=str(chat_id),
            is_digest_target=True,
            is_active=True,
        ).first()
    return binding is not None


async def ingest_channel_post(inbound: Any) -> bool:
    """Analyze one channel post as its owning workspace. True when stored.

    A chat without a registered source is skipped silently: the bot may be an
    admin of channels that nobody monitors. The listener calls this without a
    workspace context, so both the lookup and the analysis set their own.
    """
    if not getattr(inbound, "is_channel_post", False):
        return False

    item = normalize_channel_post(inbound)
    if item is None:
        return False

    found = await _find_source(inbound.chat_id)
    if found is None:
        logger.debug("ingest_source_missing")
        return False
    source_id, tenant_id, watermark = found

    # The workspace's own digest lands in its digest-target channel; when that
    # channel is also monitored as a source, analyzing our own summary would
    # pay the LLM for our own text and pollute the analytics. The watermark is
    # intentionally not advanced, so ordinary posts of the channel still ingest.
    if await _is_digest_target(tenant_id, inbound.channel, inbound.chat_id):
        logger.debug("ingest_digest_target_skipped")
        return False

    if _is_watermark_passed(item, watermark):
        logger.debug("ingest_watermark_skipped")
        return False

    from app.core.tenant_context import tenant_scope
    from app.models import Source
    from app.services.ai.analyzer import AIAnalyzer

    with tenant_scope(tenant_id):
        source = await Source.objects.select_related("platform").get(id=source_id)
        if source is None:
            return False

        analytics = await AIAnalyzer().base_analyze_content([item], source)
        if analytics is None:
            logger.warning("ingest_analysis_failed error_code=analysis_result_missing")
            return False

        with service_permission_scope("source", "update"):
            await Source.objects.update_by_id(source.id, last_item_id=item["id"])
        await Source.objects.update_last_checked(source.id)  # type: ignore[attr-defined]

    logger.info("ingest_stored")
    return True
