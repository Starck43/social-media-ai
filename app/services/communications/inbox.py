"""Tenant-scoped unified inbox service: thread list, chronological thread view, metadata search, and local state mutations (`P15-T04`).

Key invariants:
- Metadata-only per Owner Decisions D3 and D11 (`docs/audit/q-19-messaging-decisions.md`):
  search operates exclusively over metadata (`subject`, `participants`, `sender`,
  `recipient`, `channel`, `direction`, `sent_at` date range, `contact_id`).
  No message body or attachment content is stored or searched, and no `tsvector`
  or FTS configuration is used.
- Every query is strictly fenced to `current_tenant_id()` and enforces
  `require_contact_permission`.
- Chronological thread view returns messages ordered by `(sent_at ASC, id ASC)`
  with channel badges (`CHANNEL_BADGES`), `original_url`, and `is_unavailable`
  when `unavailable_at` is set (`TZ 2.5`).
- Local delete (`hide_message`) sets `is_hidden = True` locally and never issues
  a delete against the external mail/messenger provider.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.models.communications.message import (
    COMMUNICATION_CHANNELS,
    MESSAGE_DIRECTIONS,
    ContactChannelStat,
    MessageThread,
    UnifiedMessage,
)
from app.models.contacts.core import Contact
from app.models.managers.contact_manager import ContactUnavailable
from app.models.managers.unified_message_manager import require_message_permission
from app.types import ActionType

CHANNEL_BADGES: dict[str, dict[str, str]] = {
    "email": {"code": "email", "label": "Email", "badge_class": "badge-email"},
    "telegram": {"code": "telegram", "label": "Telegram", "badge_class": "badge-telegram"},
    "max": {"code": "max", "label": "MAX", "badge_class": "badge-max"},
    "phone": {"code": "phone", "label": "Телефон", "badge_class": "badge-phone"},
    "bitrix24": {"code": "bitrix24", "label": "Битрикс24", "badge_class": "badge-bitrix24"},
}


class InboxItemUnavailable(LookupError):
    """Opaque 404 error when a thread or message is missing or belongs to another workspace."""


def _message_dict(msg: UnifiedMessage) -> dict[str, Any]:
    badge = CHANNEL_BADGES.get(
        msg.channel,
        {"code": msg.channel, "label": msg.channel, "badge_class": "badge-default"},
    )
    return {
        "id": msg.id,
        "thread_id": msg.thread_id,
        "contact_id": msg.contact_id,
        "connection_id": msg.connection_id,
        "channel": msg.channel,
        "channel_label": badge["label"],
        "channel_badge": badge["badge_class"],
        "original_id": msg.original_id,
        "direction": msg.direction,
        "sent_at": msg.sent_at,
        "received_at": msg.received_at,
        "subject": msg.subject_encrypted,
        "sender": msg.sender_encrypted,
        "recipient": msg.recipient_encrypted,
        "original_url": None if msg.is_unavailable else msg.original_url,
        "content_hash": msg.content_hash,
        "is_read": bool(msg.is_read),
        "is_archived": bool(msg.is_archived),
        "is_hidden": bool(msg.is_hidden),
        "is_unavailable": msg.is_unavailable,
        "unavailable_at": msg.unavailable_at,
        "availability_status": "unavailable" if msg.is_unavailable else "available",
    }


def _thread_dict(thread: MessageThread, *, contact_name: str | None = None) -> dict[str, Any]:
    badge = CHANNEL_BADGES.get(
        thread.channel,
        {"code": thread.channel, "label": thread.channel, "badge_class": "badge-default"},
    )
    return {
        "id": thread.id,
        "connection_id": thread.connection_id,
        "contact_id": thread.contact_id,
        "contact_display_name": contact_name,
        "channel": thread.channel,
        "channel_label": badge["label"],
        "channel_badge": badge["badge_class"],
        "thread_key": thread.thread_key,
        "subject": thread.subject_encrypted,
        "participants": thread.participants,
        "last_message_at": thread.last_message_at,
        "message_count": int(thread.message_count or 0),
        "unread_count": int(thread.unread_count or 0),
        "is_read": int(thread.unread_count or 0) == 0,
        "is_archived": bool(thread.is_archived),
    }


def _matches_metadata_query(
    *,
    query_norm: str,
    subject: str | None,
    participants: list[str] | tuple[str, ...],
    sender: str | None = None,
    recipient: str | None = None,
    contact_name: str | None = None,
    channel: str | None = None,
    direction: str | None = None,
) -> bool:
    if not query_norm:
        return True
    haystacks = [
        subject or "",
        sender or "",
        recipient or "",
        contact_name or "",
        channel or "",
        direction or "",
        *participants,
    ]
    combined = " ".join(str(part).lower() for part in haystacks if part)
    tokens = [tok for tok in query_norm.split() if tok]
    return all(tok in combined for tok in tokens)


async def list_inbox_threads(
    session,
    *,
    channel: str | None = None,
    direction: str | None = None,
    contact_id: int | None = None,
    is_read: bool | None = None,
    is_archived: bool | None = False,
    since: datetime | None = None,
    until: datetime | None = None,
    q: str | None = None,
    limit: int = 50,
    before_id: int | None = None,
) -> dict[str, Any]:
    """List tenant-scoped inbox threads with metadata filtering and bounded pagination."""
    tenant_id = require_message_permission(ActionType.VIEW)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if before_id is not None and (type(before_id) is not int or before_id <= 0):
        raise ValueError("before_id must be a positive integer")
    if channel is not None and channel not in COMMUNICATION_CHANNELS:
        raise ValueError("Unsupported channel filter")
    if direction is not None and direction not in MESSAGE_DIRECTIONS:
        raise ValueError("Unsupported direction filter")
    if since is not None and since.tzinfo is None:
        raise ValueError("since must be timezone-aware")
    if until is not None and until.tzinfo is None:
        raise ValueError("until must be timezone-aware")
    if since is not None and until is not None and since > until:
        raise ValueError("since must not be after until")

    stmt = select(MessageThread).where(MessageThread.tenant_id == tenant_id)
    if channel is not None:
        stmt = stmt.where(MessageThread.channel == channel)
    if contact_id is not None:
        stmt = stmt.where(MessageThread.contact_id == int(contact_id))
    if is_archived is not None:
        stmt = stmt.where(MessageThread.is_archived.is_(bool(is_archived)))
    if before_id is not None:
        stmt = stmt.where(MessageThread.id < before_id)
    stmt = stmt.order_by(MessageThread.id.desc())

    threads = list((await session.execute(stmt)).scalars().all())
    if not threads:
        return {"items": [], "next_before_id": None}

    contacts = list(
        (
            await session.execute(
                select(Contact).where(Contact.tenant_id == tenant_id)
            )
        )
        .scalars()
        .all()
    )
    contact_names = {c.id: c.display_name_encrypted for c in contacts}

    # Optional message-level metadata filters (direction, date range, or sender/recipient metadata search)
    need_msg_scan = direction is not None or since is not None or until is not None or bool(q and q.strip())
    messages_by_thread: dict[int, list[UnifiedMessage]] = {}
    if need_msg_scan:
        msg_rows = list(
            (
                await session.execute(
                    select(UnifiedMessage).where(UnifiedMessage.tenant_id == tenant_id)
                )
            )
            .scalars()
            .all()
        )
        for m in msg_rows:
            if not m.is_hidden and m.thread_id is not None:
                messages_by_thread.setdefault(m.thread_id, []).append(m)

    query_norm = (q or "").strip().lower()
    filtered: list[dict[str, Any]] = []

    for th in threads:
        if channel is not None and th.channel != channel:
            continue
        if contact_id is not None and th.contact_id != contact_id:
            continue
        if is_archived is not None and bool(th.is_archived) != bool(is_archived):
            continue
        if before_id is not None and th.id >= before_id:
            continue
        th_is_read = int(th.unread_count or 0) == 0
        if is_read is not None and th_is_read != bool(is_read):
            continue
        if since is not None and (th.last_message_at is None or th.last_message_at < since):
            continue
        if until is not None and (th.last_message_at is None or th.last_message_at > until):
            continue

        c_name = contact_names.get(th.contact_id) if th.contact_id is not None else None
        if need_msg_scan:
            th_msgs = messages_by_thread.get(th.id, [])
            if direction is not None and not any(m.direction == direction for m in th_msgs):
                continue
            if since is not None and th_msgs and not any(m.sent_at >= since for m in th_msgs):
                continue
            if until is not None and th_msgs and not any(m.sent_at <= until for m in th_msgs):
                continue
            if query_norm:
                thread_match = _matches_metadata_query(
                    query_norm=query_norm,
                    subject=th.subject_encrypted,
                    participants=th.participants,
                    contact_name=c_name,
                    channel=th.channel,
                )
                msg_match = any(
                    _matches_metadata_query(
                        query_norm=query_norm,
                        subject=m.subject_encrypted,
                        participants=th.participants,
                        sender=m.sender_encrypted,
                        recipient=m.recipient_encrypted,
                        contact_name=c_name,
                        channel=m.channel,
                        direction=m.direction,
                    )
                    for m in th_msgs
                )
                if not (thread_match or msg_match):
                    continue

        filtered.append(_thread_dict(th, contact_name=c_name))
        if len(filtered) >= limit:
            break

    return {
        "items": filtered,
        "next_before_id": filtered[-1]["id"] if len(filtered) == limit else None,
    }


async def get_thread_detail(
    session,
    thread_id: int,
    *,
    include_hidden: bool = False,
) -> dict[str, Any]:
    """Return a single inbox thread and its chronological messages (`sent_at ASC, id ASC`)."""
    tenant_id = require_message_permission(ActionType.VIEW)
    if type(thread_id) is not int or thread_id <= 0:
        raise InboxItemUnavailable("Thread unavailable")

    thread = (
        await session.execute(
            select(MessageThread).where(
                MessageThread.tenant_id == tenant_id,
                MessageThread.id == thread_id,
            )
        )
    ).scalar_one_or_none()
    if thread is None:
        raise InboxItemUnavailable("Thread unavailable")

    contact_name = None
    channel_stats: list[dict[str, Any]] = []
    if thread.contact_id is not None:
        contact = (
            await session.execute(
                select(Contact).where(
                    Contact.tenant_id == tenant_id,
                    Contact.id == thread.contact_id,
                )
            )
        ).scalar_one_or_none()
        if contact is not None:
            contact_name = contact.display_name_encrypted
        stat_rows = list(
            (
                await session.execute(
                    select(ContactChannelStat)
                    .where(
                        ContactChannelStat.tenant_id == tenant_id,
                        ContactChannelStat.contact_id == thread.contact_id,
                    )
                    .order_by(ContactChannelStat.channel.asc())
                )
            )
            .scalars()
            .all()
        )
        channel_stats = [
            {
                "channel": s.channel,
                "inbound_count": int(s.inbound_count or 0),
                "outbound_count": int(s.outbound_count or 0),
                "last_inbound_at": s.last_inbound_at,
                "last_outbound_at": s.last_outbound_at,
                "last_message_at": s.last_message_at,
            }
            for s in stat_rows
        ]

    msg_rows = list(
        (
            await session.execute(
                select(UnifiedMessage)
                .where(
                    UnifiedMessage.tenant_id == tenant_id,
                    UnifiedMessage.thread_id == thread_id,
                )
                .order_by(UnifiedMessage.sent_at.asc(), UnifiedMessage.id.asc())
            )
        )
        .scalars()
        .all()
    )
    visible_msgs = [m for m in msg_rows if include_hidden or not m.is_hidden]
    visible_msgs.sort(key=lambda m: (m.sent_at, m.id))

    return {
        "thread": _thread_dict(thread, contact_name=contact_name),
        "messages": [_message_dict(m) for m in visible_msgs],
        "contact_channel_stats": channel_stats,
    }


async def list_contact_messages(
    session,
    contact_id: int,
    *,
    channel: str | None = None,
    limit: int = 50,
    before_id: int | None = None,
) -> dict[str, Any]:
    """Return chronological unified messages for a specific contact (`TZ AC`)."""
    tenant_id = require_message_permission(ActionType.VIEW)
    if type(contact_id) is not int or contact_id <= 0:
        raise ContactUnavailable("Contact unavailable")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")

    contact = (
        await session.execute(
            select(Contact).where(
                Contact.tenant_id == tenant_id,
                Contact.id == contact_id,
            )
        )
    ).scalar_one_or_none()
    if contact is None:
        raise ContactUnavailable("Contact unavailable")

    rows = list(
        (
            await session.execute(
                select(UnifiedMessage)
                .where(
                    UnifiedMessage.tenant_id == tenant_id,
                    UnifiedMessage.contact_id == contact_id,
                )
                .order_by(UnifiedMessage.sent_at.asc(), UnifiedMessage.id.asc())
            )
        )
        .scalars()
        .all()
    )
    filtered = [
        m
        for m in rows
        if not m.is_hidden
        and (channel is None or m.channel == channel)
        and (before_id is None or m.id < before_id)
    ]
    filtered.sort(key=lambda m: (m.sent_at, m.id))
    sliced = filtered[:limit]
    return {
        "contact_id": contact_id,
        "items": [_message_dict(m) for m in sliced],
        "next_before_id": sliced[-1].id if len(sliced) == limit else None,
    }


async def mark_thread_read(
    session,
    *,
    thread_id: int,
    is_read: bool = True,
) -> dict[str, Any]:
    """Mark a thread and its messages read or unread within the active workspace."""
    tenant_id = require_message_permission(ActionType.UPDATE)
    if type(thread_id) is not int or thread_id <= 0:
        raise InboxItemUnavailable("Thread unavailable")

    thread = (
        await session.execute(
            select(MessageThread).where(
                MessageThread.tenant_id == tenant_id,
                MessageThread.id == thread_id,
            )
        )
    ).scalar_one_or_none()
    if thread is None:
        raise InboxItemUnavailable("Thread unavailable")

    messages = list(
        (
            await session.execute(
                select(UnifiedMessage).where(
                    UnifiedMessage.tenant_id == tenant_id,
                    UnifiedMessage.thread_id == thread_id,
                )
            )
        )
        .scalars()
        .all()
    )
    updated_count = 0
    for msg in messages:
        if not msg.is_hidden and bool(msg.is_read) != bool(is_read):
            msg.is_read = bool(is_read)
            updated_count += 1

    if is_read:
        thread.unread_count = 0
    else:
        unread = sum(1 for m in messages if not m.is_hidden and not m.is_read)
        thread.unread_count = unread
    await session.flush()

    return {
        "thread_id": thread.id,
        "is_read": int(thread.unread_count or 0) == 0,
        "unread_count": int(thread.unread_count or 0),
        "updated_messages": updated_count,
    }


async def archive_thread(
    session,
    *,
    thread_id: int,
    is_archived: bool = True,
) -> dict[str, Any]:
    """Archive or unarchive a thread locally within the active workspace."""
    tenant_id = require_message_permission(ActionType.UPDATE)
    if type(thread_id) is not int or thread_id <= 0:
        raise InboxItemUnavailable("Thread unavailable")

    thread = (
        await session.execute(
            select(MessageThread).where(
                MessageThread.tenant_id == tenant_id,
                MessageThread.id == thread_id,
            )
        )
    ).scalar_one_or_none()
    if thread is None:
        raise InboxItemUnavailable("Thread unavailable")

    thread.is_archived = bool(is_archived)
    messages = list(
        (
            await session.execute(
                select(UnifiedMessage).where(
                    UnifiedMessage.tenant_id == tenant_id,
                    UnifiedMessage.thread_id == thread_id,
                )
            )
        )
        .scalars()
        .all()
    )
    for msg in messages:
        msg.is_archived = bool(is_archived)
    await session.flush()

    return {
        "thread_id": thread.id,
        "is_archived": bool(thread.is_archived),
    }


async def hide_message_locally(
    session,
    *,
    message_id: int,
    is_hidden: bool = True,
) -> dict[str, Any]:
    """Hide or unhide a message locally (`P15-T04`: delete = local hide, never provider delete)."""
    tenant_id = require_message_permission(ActionType.UPDATE)
    if type(message_id) is not int or message_id <= 0:
        raise InboxItemUnavailable("Message unavailable")

    msg = (
        await session.execute(
            select(UnifiedMessage).where(
                UnifiedMessage.tenant_id == tenant_id,
                UnifiedMessage.id == message_id,
            )
        )
    ).scalar_one_or_none()
    if msg is None:
        raise InboxItemUnavailable("Message unavailable")

    was_hidden = bool(msg.is_hidden)
    msg.is_hidden = bool(is_hidden)
    if msg.thread_id is not None and was_hidden != bool(is_hidden):
        thread = (
            await session.execute(
                select(MessageThread).where(
                    MessageThread.tenant_id == tenant_id,
                    MessageThread.id == msg.thread_id,
                )
            )
        ).scalar_one_or_none()
        if thread is not None:
            if is_hidden:
                thread.message_count = max(0, int(thread.message_count or 0) - 1)
                if not msg.is_read:
                    thread.unread_count = max(0, int(thread.unread_count or 0) - 1)
            else:
                thread.message_count = int(thread.message_count or 0) + 1
                if not msg.is_read:
                    thread.unread_count = int(thread.unread_count or 0) + 1

    await session.flush()
    return {
        "message_id": msg.id,
        "thread_id": msg.thread_id,
        "is_hidden": bool(msg.is_hidden),
        "provider_deleted": False,
    }
