"""Bot API channel posts enter the existing durable staging/replay pipeline.

No history pull, downloads, inline LLM or sender activation. A source watermark
is not an admission receipt: an unseen lower message must still stage.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.services.monitoring.staging import admit_telegram_items
from app.utils.content_attachments import normalize_attachments

logger = logging.getLogger(__name__)


def normalize_channel_post(inbound: Any) -> Optional[dict[str, Any]]:
    """Map one Telegram update to the shared normalized content-item contract.

    Keys mirror `VKClient._normalize_response` / `TelegramClient._normalize_response`
    so the analyzer treats the item identically.
    """
    raw = getattr(inbound, "raw", None) or {}
    post = raw.get("channel_post") or raw.get("message") or {}
    if type(post) is not dict:
        return None
    identity = post.get("message_id")
    if type(identity) is not int or identity <= 0:
        raise ValueError("content_identity_invalid")
    text = post.get("text") or post.get("caption") or ""
    if type(text) is not str:
        raise ValueError("content_text_invalid")
    # File references remain placeholders, never bot-token/file URLs.
    attachments = normalize_attachments([
        {"type": kind, "url": None}
        for key, kind in (("photo", "photo"), ("video", "video"),
                          ("animation", "video"), ("video_note", "video"),
                          ("document", "unknown"), ("audio", "unknown"),
                          ("voice", "unknown"), ("sticker", "unknown"),
                          ("paid_media", "unknown"), ("contact", "unknown"),
                          ("location", "unknown"), ("venue", "unknown"), ("poll", "unknown"))
        if key in post
    ])
    if not text and not attachments:
        return None
    published = post.get("date")
    published_ts = float(published) if type(published) in (int, float, str) else None

    return {
        "id": str(post.get("message_id", "")),
        "external_id": f"{inbound.chat_id}_{post.get('message_id', '')}",
        "text": text,
        "attachments": attachments,
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
        sources = list(await Source.objects.filter(
            platform_id=platform.id, external_id=str(chat_id), is_active=True,
        ).limit(2))
        if not sources:
            return None
        if len(sources) != 1:
            raise RuntimeError("content_source_ambiguous")
        source = sources[0]
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
    """Acknowledge committed admission/duplicate; False is an intentional skip.

    Errors propagate to the listener so getUpdates cannot acknowledge a failed
    storage/queue commit. Analysis is deferred to existing handle_analyze.
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
    source_id, tenant_id, _watermark = found
    if await _is_digest_target(tenant_id, inbound.channel, inbound.chat_id):
        logger.debug("ingest_digest_target_skipped")
        return False

    from app.core.tenant_context import tenant_scope
    from app.models import Source

    with tenant_scope(tenant_id):
        source = await Source.objects.select_related("platform").get(id=source_id)
        if source is None:
            raise RuntimeError("content_source_unavailable")
        await admit_telegram_items([item], source, wake_analysis=True)
    logger.info("ingest_admitted")
    return True
