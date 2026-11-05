"""Idempotent metadata-only communication ingestion service (`P15-T02`).

Responsibilities:
- Enforce workspace isolation (`current_tenant_id()`) and connection ownership.
- Enforce D3 (metadata-only): persist headers, encrypted sender/recipient/subject,
  `original_url`, and `content_hash` (`sha256:<64hex>`). Never persist message
  bodies or attachments.
- Enforce P12-T14 personal Telegram hard-off: personal Telegram connectors are
  refused unless explicitly workspace-authorized and active; bot connections
  ingest normally.
- Deduplicate idempotently on `(tenant_id, connection_id, channel, original_id)` (R-09)
  and support RFC `Message-ID` hash re-binding on IMAP `UIDVALIDITY` resets (`P15-T01`).
- Group messages into `MessageThread` by RFC `References`/`In-Reply-To` or chat ID.
- Resolve or create `Contact` + `ContactIdentity` via blind-index lookup without
  silent auto-merging, and maintain `ContactChannelStat` counters.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from typing import Any

from sqlalchemy import delete, select

from app.core.pii import PIIError
from app.core.tenant_context import TenantContextError, current_tenant_id
from app.models.communications.message import (
    COMMUNICATION_CHANNELS,
    MESSAGE_DIRECTIONS,
    ContactChannelStat,
    MessageThread,
    UnifiedMessage,
)
from app.models.contacts.core import Contact, ContactIdentity
from app.models.integrations.authorization import PersonalConnectorAuthorization, WorkspaceAuthorizationGrant
from app.models.integrations.connection import WorkspaceConnection
from app.services.contacts.identity import identity_index, normalized_identity

_SUBJECT_PREFIX_RE = re.compile(
    r"^\s*(?:(?:(?:re|fwd|fw|ответ|пересл)\s*(?:\[\d+\])?|\[\d+\])\s*:\s*)+",
    re.IGNORECASE,
)
_CONTENT_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MSG_ID_TOKEN_RE = re.compile(r"<([^<>]+)>")


class CommunicationIngestError(ValueError):
    """Raised when an ingestion envelope violates schema or security invariants."""


class PersonalConnectorDisabledError(PermissionError):
    """Raised when personal Telegram ingestion is attempted while hard-off or revoked (P12-T14)."""


@dataclass(frozen=True)
class IngestMessageInput:
    connection_id: int
    channel: str
    original_id: str
    direction: str
    sent_at: datetime
    received_at: datetime | None = None
    subject: str | None = None
    sender: str | None = None
    recipient: str | None = None
    participants: tuple[str, ...] = ()
    original_url: str | None = None
    content_hash: str | None = None
    body_for_hash_only: str | bytes | None = None
    external_message_id: str | None = None
    in_reply_to: str | None = None
    references: tuple[str, ...] = ()
    chat_id: str | None = None
    contact_identity_kind: str | None = None
    contact_identity_value: str | None = None
    contact_display_name: str | None = None
    contact_source_native_id: str | None = None
    is_read: bool = False
    dedupe_by_external_message_id_hash: bool = False


@dataclass(frozen=True)
class IngestResult:
    message: UnifiedMessage
    thread: MessageThread
    contact_id: int | None
    created: bool
    uid_rebound: bool = False


def compute_content_hash(payload: str | bytes | None) -> str | None:
    """Compute a `sha256:<64hex>` digest in memory without persisting the payload."""
    if payload is None:
        return None
    raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def normalize_message_id(msg_id: str | None) -> str | None:
    """Normalize an RFC 5322 Message-ID token for deterministic hashing and threading."""
    if not msg_id or not isinstance(msg_id, str):
        return None
    cleaned = msg_id.strip()
    if not cleaned:
        return None
    match = _MSG_ID_TOKEN_RE.search(cleaned)
    token = match.group(1).strip() if match else cleaned.strip("<> \t\r\n")
    return token.lower() if token else None


def hash_message_id(msg_id: str | None) -> str | None:
    """Return a 64-char hex SHA-256 digest of a normalized RFC Message-ID."""
    normalized = normalize_message_id(msg_id)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def extract_reference_ids(raw_references: str | list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    """Parse an RFC References header or sequence into normalized Message-ID tokens."""
    if not raw_references:
        return ()
    if isinstance(raw_references, str):
        tokens = _MSG_ID_TOKEN_RE.findall(raw_references) or raw_references.split()
    else:
        tokens = []
        for item in raw_references:
            if item:
                found = _MSG_ID_TOKEN_RE.findall(str(item))
                tokens.extend(found if found else [str(item)])
    normalized: list[str] = []
    for tok in tokens:
        norm = normalize_message_id(tok)
        if norm and norm not in normalized:
            normalized.append(norm)
    return tuple(normalized)


def normalize_subject_for_thread(subject: str | None) -> str:
    """Strip reply/forward prefixes and collapse whitespace for fallback thread keys."""
    if not subject:
        return ""
    stripped = _SUBJECT_PREFIX_RE.sub("", subject.strip())
    return " ".join(stripped.split()).lower()


def derive_thread_key(item: IngestMessageInput) -> str:
    """Derive a bounded, deterministic thread key (<255 chars) from message metadata."""
    if item.channel == "email":
        refs = extract_reference_ids(item.references)
        root_ref = refs[0] if refs else normalize_message_id(item.in_reply_to)
        if not root_ref:
            root_ref = normalize_message_id(item.external_message_id)
        if root_ref:
            if len(root_ref) > 200:
                root_ref = hashlib.sha256(root_ref.encode("utf-8")).hexdigest()
            return f"email:msgid:{root_ref}"
        norm_subj = normalize_subject_for_thread(item.subject)
        if norm_subj:
            subj_hash = hashlib.sha256(norm_subj.encode("utf-8")).hexdigest()[:32]
            return f"email:subj:{subj_hash}"
        return f"email:orig:{item.original_id[:200]}"

    if item.chat_id:
        safe_chat = str(item.chat_id).strip()[:200]
        return f"{item.channel}:chat:{safe_chat}"

    return f"{item.channel}:orig:{item.original_id[:200]}"


def _require_tenant() -> int:
    tenant_id = current_tenant_id()
    if type(tenant_id) is not int or tenant_id <= 0:
        raise TenantContextError("Unified communication ingestion requires an explicit workspace scope")
    return tenant_id


def _validate_input(item: IngestMessageInput) -> tuple[datetime, datetime, str | None]:
    if type(item.connection_id) is not int or item.connection_id <= 0:
        raise CommunicationIngestError("connection_id must be a positive integer")
    if item.channel not in COMMUNICATION_CHANNELS:
        raise CommunicationIngestError(f"Unsupported communication channel: {item.channel!r}")
    if item.direction not in MESSAGE_DIRECTIONS:
        raise CommunicationIngestError(f"Unsupported message direction: {item.direction!r}")
    if not item.original_id or not str(item.original_id).strip():
        raise CommunicationIngestError("original_id is required")
    if len(str(item.original_id).strip()) > 255:
        raise CommunicationIngestError("original_id exceeds 255 characters")
    if not isinstance(item.sent_at, datetime) or item.sent_at.tzinfo is None:
        raise CommunicationIngestError("sent_at must be a timezone-aware datetime")
    received_at = item.received_at or item.sent_at
    if not isinstance(received_at, datetime) or received_at.tzinfo is None:
        raise CommunicationIngestError("received_at must be a timezone-aware datetime")

    content_hash = item.content_hash
    if content_hash is None and item.body_for_hash_only is not None:
        content_hash = compute_content_hash(item.body_for_hash_only)
    if content_hash is not None and not _CONTENT_HASH_RE.fullmatch(content_hash):
        raise CommunicationIngestError("content_hash must match 'sha256:<64 lowercase hex>'")

    return item.sent_at, received_at, content_hash


async def _verify_connection(session, *, tenant_id: int, connection_id: int, channel: str) -> WorkspaceConnection:
    conn = (
        await session.execute(
            select(WorkspaceConnection).where(
                WorkspaceConnection.tenant_id == tenant_id,
                WorkspaceConnection.id == connection_id,
            )
        )
    ).scalar_one_or_none()
    if conn is None:
        raise CommunicationIngestError("Workspace connection not found in active tenant")
    if not conn.is_active or conn.state == "disabled":
        raise CommunicationIngestError("Workspace connection is disabled")

    # P12-T14 personal Telegram hard-off enforcement:
    config = conn.config if isinstance(conn.config, dict) else {}
    is_personal_tg = channel == "telegram" and (
        conn.auth_method in ("personal_telegram", "telegram_personal", "mtproto_session")
        or config.get("connector_mode") == "personal"
        or config.get("personal_telegram") is True
    )
    if is_personal_tg:
        # Personal Telegram connectors are hard-off by default unless explicitly enabled with active grant
        if not config.get("personal_telegram_enabled", False):
            raise PersonalConnectorDisabledError("Personal Telegram ingestion is hard-off for this workspace")
        auth_id = config.get("authorization_id")
        if type(auth_id) is not int or auth_id <= 0:
            raise PersonalConnectorDisabledError("Personal Telegram connector lacks a valid authorization grant")
        grant_row = (
            await session.execute(
                select(WorkspaceAuthorizationGrant, PersonalConnectorAuthorization)
                .join(
                    PersonalConnectorAuthorization,
                    PersonalConnectorAuthorization.id == WorkspaceAuthorizationGrant.authorization_id,
                )
                .where(
                    WorkspaceAuthorizationGrant.tenant_id == tenant_id,
                    WorkspaceAuthorizationGrant.authorization_id == auth_id,
                    WorkspaceAuthorizationGrant.is_active.is_(True),
                    PersonalConnectorAuthorization.state == "active",
                    WorkspaceAuthorizationGrant.authorization_revision == PersonalConnectorAuthorization.revision,
                )
            )
        ).first()
        if grant_row is None:
            raise PersonalConnectorDisabledError("Personal Telegram authorization is revoked or out of revision")

    return conn


def _infer_identity_from_item(item: IngestMessageInput) -> tuple[str | None, str | None, str | None]:
    """Return (kind, value, display_name) for the external counterpart of a message."""
    if item.contact_identity_kind and item.contact_identity_value:
        return item.contact_identity_kind, item.contact_identity_value, item.contact_display_name

    counterpart = item.sender if item.direction == "inbound" else item.recipient
    if not counterpart:
        return None, None, item.contact_display_name

    if item.channel == "email":
        display_name, email_addr = parseaddr(counterpart)
        candidate = email_addr or counterpart.strip()
        if "@" in candidate:
            return "email", candidate, item.contact_display_name or (display_name.strip() or None)
    elif item.channel == "telegram":
        raw = counterpart.strip()
        if raw.startswith("@") or (not raw.isdigit() and len(raw) >= 3):
            return "telegram_username", raw, item.contact_display_name
        if raw.isdigit():
            return "telegram_id", raw, item.contact_display_name
    elif item.channel == "max":
        raw = counterpart.strip()
        if raw:
            return "max_id", raw, item.contact_display_name
    elif item.channel == "phone":
        return "phone", counterpart.strip(), item.contact_display_name

    return None, None, item.contact_display_name


def follow_merge_chain(contact_id: int, links: dict[int, int | None], max_depth: int = 16) -> int:
    """Resolve a merged contact ID to its active survivor contact ID."""
    current = contact_id
    visited: set[int] = {current}
    for _ in range(max_depth):
        nxt = links.get(current)
        if nxt is None:
            return current
        if nxt in visited:
            break
        visited.add(nxt)
        current = nxt
    return current


async def _resolve_or_create_contact(
    session,
    *,
    tenant_id: int,
    connection_id: int,
    item: IngestMessageInput,
    sent_at: datetime,
) -> int | None:
    kind, raw_value, display_name = _infer_identity_from_item(item)
    if not kind or not raw_value:
        return None
    try:
        normalized_value = normalized_identity(kind, raw_value)
        b_index = identity_index(kind, raw_value)
    except (ValueError, PIIError):
        return None

    existing_identity = (
        await session.execute(
            select(ContactIdentity)
            .where(
                ContactIdentity.tenant_id == tenant_id,
                ContactIdentity.kind == kind,
                ContactIdentity.blind_index == b_index,
                ContactIdentity.connection_id == connection_id,
            )
            .order_by(ContactIdentity.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if existing_identity is not None:
        contact_id = existing_identity.contact_id
        contact = (
            await session.execute(
                select(Contact).where(
                    Contact.tenant_id == tenant_id,
                    Contact.id == contact_id,
                )
            )
        ).scalar_one_or_none()
        if contact is not None and contact.merged_into_id is not None:
            # Follow merge chain to the active survivor contact
            chain_rows = (
                await session.execute(
                    select(Contact.id, Contact.merged_into_id).where(Contact.tenant_id == tenant_id)
                )
            ).all()
            links = {row[0]: row[1] for row in chain_rows}
            contact_id = follow_merge_chain(contact.id, links)
            contact = (
                await session.execute(
                    select(Contact).where(
                        Contact.tenant_id == tenant_id,
                        Contact.id == contact_id,
                    )
                )
            ).scalar_one_or_none()
        if contact is not None:
            if contact.last_active_at is None or sent_at >= contact.last_active_at:
                contact.last_active_at = sent_at
                contact.last_active_channel = item.channel
            return contact.id

    # Create a new unmerged Contact + ContactIdentity
    new_contact = Contact(
        tenant_id=tenant_id,
        display_name_encrypted=(display_name or normalized_value)[:255],
        last_active_channel=item.channel,
        last_active_at=sent_at,
    )
    session.add(new_contact)
    await session.flush()

    bound_conn_id = connection_id if kind in ("bitrix24_contact", "bitrix24_company", "bitrix24_lead") else connection_id
    new_identity = ContactIdentity(
        tenant_id=tenant_id,
        contact_id=new_contact.id,
        kind=kind,
        connection_id=bound_conn_id,
        value_encrypted=normalized_value,
        blind_index=b_index,
        verified=False,
        source_native_id_encrypted=item.contact_source_native_id,
    )
    session.add(new_identity)
    await session.flush()
    return new_contact.id


def _merge_participants(existing_encrypted: str | None, item: IngestMessageInput) -> str | None:
    participants: list[str] = []
    if existing_encrypted:
        try:
            loaded = json.loads(existing_encrypted)
            if isinstance(loaded, list):
                participants.extend(str(p) for p in loaded if p)
        except (ValueError, TypeError):
            pass
    for candidate in (item.sender, item.recipient, *item.participants):
        if candidate:
            cleaned = str(candidate).strip()
            if cleaned and cleaned not in participants:
                participants.append(cleaned)
    if not participants:
        return None
    return json.dumps(participants[:50], ensure_ascii=False)


async def _resolve_or_create_thread(
    session,
    *,
    tenant_id: int,
    item: IngestMessageInput,
    contact_id: int | None,
    sent_at: datetime,
    is_new_message: bool,
) -> MessageThread:
    t_key = derive_thread_key(item)
    thread = (
        await session.execute(
            select(MessageThread).where(
                MessageThread.tenant_id == tenant_id,
                MessageThread.connection_id == item.connection_id,
                MessageThread.channel == item.channel,
                MessageThread.thread_key == t_key,
            )
        )
    ).scalar_one_or_none()

    unread_delta = 1 if (is_new_message and item.direction == "inbound" and not item.is_read) else 0

    if thread is None:
        thread = MessageThread(
            tenant_id=tenant_id,
            connection_id=item.connection_id,
            contact_id=contact_id,
            channel=item.channel,
            thread_key=t_key,
            subject_encrypted=item.subject[:1024] if item.subject else None,
            participants_encrypted=_merge_participants(None, item),
            last_message_at=sent_at,
            message_count=1 if is_new_message else 0,
            unread_count=unread_delta,
            is_archived=False,
        )
        session.add(thread)
        await session.flush()
        return thread

    if contact_id is not None and thread.contact_id is None:
        thread.contact_id = contact_id
    if item.subject and not thread.subject_encrypted:
        thread.subject_encrypted = item.subject[:1024]
    merged_participants = _merge_participants(thread.participants_encrypted, item)
    if merged_participants:
        thread.participants_encrypted = merged_participants
    if thread.last_message_at is None or sent_at >= thread.last_message_at:
        thread.last_message_at = sent_at
    if is_new_message:
        thread.message_count = int(thread.message_count or 0) + 1
        thread.unread_count = int(thread.unread_count or 0) + unread_delta
    await session.flush()
    return thread


async def _update_contact_channel_stats(
    session,
    *,
    tenant_id: int,
    contact_id: int,
    channel: str,
    direction: str,
    sent_at: datetime,
) -> ContactChannelStat:
    stat = (
        await session.execute(
            select(ContactChannelStat).where(
                ContactChannelStat.tenant_id == tenant_id,
                ContactChannelStat.contact_id == contact_id,
                ContactChannelStat.channel == channel,
            )
        )
    ).scalar_one_or_none()

    if stat is None:
        stat = ContactChannelStat(
            tenant_id=tenant_id,
            contact_id=contact_id,
            channel=channel,
            inbound_count=1 if direction == "inbound" else 0,
            outbound_count=1 if direction == "outbound" else 0,
            last_inbound_at=sent_at if direction == "inbound" else None,
            last_outbound_at=sent_at if direction == "outbound" else None,
            last_message_at=sent_at,
        )
        session.add(stat)
        await session.flush()
        return stat

    if direction == "inbound":
        stat.inbound_count = int(stat.inbound_count or 0) + 1
        if stat.last_inbound_at is None or sent_at >= stat.last_inbound_at:
            stat.last_inbound_at = sent_at
    else:
        stat.outbound_count = int(stat.outbound_count or 0) + 1
        if stat.last_outbound_at is None or sent_at >= stat.last_outbound_at:
            stat.last_outbound_at = sent_at

    if stat.last_message_at is None or sent_at >= stat.last_message_at:
        stat.last_message_at = sent_at
    await session.flush()
    return stat


def _select_rebind_candidate(
    candidates: list[UnifiedMessage],
    original_id: str,
    *,
    require_same_folder: bool = False,
) -> UnifiedMessage | None:
    """Select the best candidate row to re-bind on a UIDVALIDITY reset (B-2, B-3)."""
    if not candidates:
        return None
    ordered = sorted(candidates, key=lambda m: (m.id is None, m.id or 0))
    parts = original_id.split(":", 2)
    folder_prefix = f"{parts[0]}:" if len(parts) >= 2 else None
    uv_prefix = f"{parts[0]}:{parts[1]}:" if len(parts) >= 3 else None

    if folder_prefix:
        same_folder = [m for m in ordered if str(m.original_id or "").startswith(folder_prefix)]
        if uv_prefix:
            unrebound_same_folder = [
                m for m in same_folder if not str(m.original_id or "").startswith(uv_prefix)
            ]
            if unrebound_same_folder:
                return unrebound_same_folder[0]
        if same_folder:
            return same_folder[0]
        if require_same_folder:
            return None

    if uv_prefix:
        unrebound_any = [m for m in ordered if not str(m.original_id or "").startswith(uv_prefix)]
        if unrebound_any:
            return unrebound_any[0]
    return ordered[0]


async def _ensure_thread_and_sync_flags(
    session,
    *,
    tenant_id: int,
    item: IngestMessageInput,
    target_msg: UnifiedMessage,
    sent_at: datetime,
) -> MessageThread:
    thread = None
    if target_msg.thread_id is not None:
        thread = (
            await session.execute(
                select(MessageThread).where(
                    MessageThread.tenant_id == tenant_id,
                    MessageThread.id == target_msg.thread_id,
                )
            )
        ).scalar_one_or_none()
    if thread is None:
        thread = await _resolve_or_create_thread(
            session,
            tenant_id=tenant_id,
            item=item,
            contact_id=target_msg.contact_id,
            sent_at=sent_at,
            is_new_message=False,
        )
        target_msg.thread_id = thread.id
    if item.is_read and not target_msg.is_read:
        target_msg.is_read = True
        if target_msg.direction == "inbound":
            thread.unread_count = max(0, int(thread.unread_count or 0) - 1)
    if target_msg.unavailable_at is not None:
        target_msg.unavailable_at = None
    await session.flush()
    return thread


async def ingest(session, item: IngestMessageInput) -> IngestResult:
    """Idempotently ingest a single message metadata envelope into the active workspace."""
    tenant_id = _require_tenant()
    sent_at, received_at, content_hash = _validate_input(item)
    await _verify_connection(session, tenant_id=tenant_id, connection_id=item.connection_id, channel=item.channel)

    original_id = str(item.original_id).strip()
    ext_msg_hash = hash_message_id(item.external_message_id)
    reply_hash = hash_message_id(item.in_reply_to)

    # 1. Primary R-09 idempotency lookup by (tenant_id, connection_id, channel, original_id)
    existing = (
        await session.execute(
            select(UnifiedMessage).where(
                UnifiedMessage.tenant_id == tenant_id,
                UnifiedMessage.connection_id == item.connection_id,
                UnifiedMessage.channel == item.channel,
                UnifiedMessage.original_id == original_id,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        thread = await _ensure_thread_and_sync_flags(
            session,
            tenant_id=tenant_id,
            item=item,
            target_msg=existing,
            sent_at=sent_at,
        )
        return IngestResult(
            message=existing,
            thread=thread,
            contact_id=existing.contact_id,
            created=False,
            uid_rebound=False,
        )

    # 2. Optional RFC Message-ID hash (or header content_hash fallback) deduplication for IMAP UIDVALIDITY resets
    if item.dedupe_by_external_message_id_hash:
        by_hash: UnifiedMessage | None = None
        if ext_msg_hash is not None:
            candidates = (
                await session.execute(
                    select(UnifiedMessage)
                    .where(
                        UnifiedMessage.tenant_id == tenant_id,
                        UnifiedMessage.connection_id == item.connection_id,
                        UnifiedMessage.channel == item.channel,
                        UnifiedMessage.external_message_id_hash == ext_msg_hash,
                    )
                    .order_by(UnifiedMessage.id.asc())
                )
            ).scalars().all()
            by_hash = _select_rebind_candidate(list(candidates), original_id, require_same_folder=False)
        elif content_hash is not None:
            candidates = (
                await session.execute(
                    select(UnifiedMessage)
                    .where(
                        UnifiedMessage.tenant_id == tenant_id,
                        UnifiedMessage.connection_id == item.connection_id,
                        UnifiedMessage.channel == item.channel,
                        UnifiedMessage.content_hash == content_hash,
                    )
                    .order_by(UnifiedMessage.id.asc())
                )
            ).scalars().all()
            by_hash = _select_rebind_candidate(
                list(candidates),
                original_id,
                require_same_folder=bool(":" in original_id),
            )

        if by_hash is not None:
            by_hash.original_id = original_id
            if item.original_url:
                by_hash.original_url = item.original_url[:1024]
            thread = await _ensure_thread_and_sync_flags(
                session,
                tenant_id=tenant_id,
                item=item,
                target_msg=by_hash,
                sent_at=sent_at,
            )
            return IngestResult(
                message=by_hash,
                thread=thread,
                contact_id=by_hash.contact_id,
                created=False,
                uid_rebound=True,
            )

    # 3. Resolve or create Contact + ContactIdentity
    contact_id = await _resolve_or_create_contact(
        session,
        tenant_id=tenant_id,
        connection_id=item.connection_id,
        item=item,
        sent_at=sent_at,
    )

    # 4. Resolve or create MessageThread
    thread = await _resolve_or_create_thread(
        session,
        tenant_id=tenant_id,
        item=item,
        contact_id=contact_id,
        sent_at=sent_at,
        is_new_message=True,
    )

    # 5. Insert UnifiedMessage
    message = UnifiedMessage(
        tenant_id=tenant_id,
        connection_id=item.connection_id,
        contact_id=contact_id,
        thread_id=thread.id,
        channel=item.channel,
        original_id=original_id,
        external_message_id_hash=ext_msg_hash,
        in_reply_to_hash=reply_hash,
        direction=item.direction,
        sent_at=sent_at,
        received_at=received_at,
        subject_encrypted=item.subject[:1024] if item.subject else None,
        sender_encrypted=item.sender[:1024] if item.sender else None,
        recipient_encrypted=item.recipient[:2048] if item.recipient else None,
        original_url=item.original_url[:1024] if item.original_url else None,
        content_hash=content_hash,
        is_read=bool(item.is_read or item.direction == "outbound"),
        is_archived=False,
        is_hidden=False,
        unavailable_at=None,
    )
    session.add(message)
    await session.flush()

    # 6. Update ContactChannelStat
    if contact_id is not None:
        await _update_contact_channel_stats(
            session,
            tenant_id=tenant_id,
            contact_id=contact_id,
            channel=item.channel,
            direction=item.direction,
            sent_at=sent_at,
        )

    return IngestResult(
        message=message,
        thread=thread,
        contact_id=contact_id,
        created=True,
        uid_rebound=False,
    )


async def ingest_telegram_update(
    session,
    *,
    connection_id: int,
    update: dict[str, Any],
    direction: str = "inbound",
) -> IngestResult | None:
    """Ingest a Telegram Bot API update metadata envelope (D3: text is hashed only, never stored)."""
    deleted = update.get("deleted_message")
    if isinstance(deleted, dict):
        del_chat = deleted.get("chat") if isinstance(deleted.get("chat"), dict) else {}
        del_chat_id = del_chat.get("id") or deleted.get("chat_id")
        del_msg_id = deleted.get("message_id")
        if del_chat_id is not None and del_msg_id is not None:
            await mark_message_unavailable(
                session,
                connection_id=connection_id,
                channel="telegram",
                original_id=f"{del_chat_id}:{del_msg_id}",
            )
        return None

    msg = update.get("message") or update.get("edited_message") or update.get("channel_post")
    if not isinstance(msg, dict):
        return None
    message_id = msg.get("message_id")
    chat = msg.get("chat") if isinstance(msg.get("chat"), dict) else {}
    chat_id = chat.get("id")
    if message_id is None or chat_id is None:
        return None
    if msg.get("is_deleted"):
        await mark_message_unavailable(
            session,
            connection_id=connection_id,
            channel="telegram",
            original_id=f"{chat_id}:{message_id}",
        )
        return None

    sender_obj = msg.get("from") if isinstance(msg.get("from"), dict) else {}
    sender_id = str(sender_obj.get("id") or chat_id)
    username = sender_obj.get("username")
    first_name = sender_obj.get("first_name") or ""
    last_name = sender_obj.get("last_name") or ""
    display_name = " ".join(part for part in (first_name, last_name) if part).strip() or username or sender_id

    ts_raw = msg.get("date")
    sent_at = (
        datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
        if isinstance(ts_raw, (int, float))
        else datetime.now(timezone.utc)
    )
    text_body = msg.get("text") or msg.get("caption") or ""
    reply_to = msg.get("reply_to_message")
    in_reply_to = (
        f"tg:{chat_id}:{reply_to.get('message_id')}"
        if isinstance(reply_to, dict) and reply_to.get("message_id") is not None
        else None
    )
    original_url = (
        f"https://t.me/c/{str(chat_id).removeprefix('-100')}/{message_id}"
        if str(chat_id).startswith("-100")
        else None
    )

    envelope = IngestMessageInput(
        connection_id=connection_id,
        channel="telegram",
        original_id=f"{chat_id}:{message_id}",
        direction=direction,
        sent_at=sent_at,
        received_at=datetime.now(timezone.utc),
        subject=chat.get("title"),
        sender=f"@{username}" if username else sender_id,
        recipient=str(chat_id),
        participants=(f"@{username}" if username else sender_id, str(chat_id)),
        original_url=original_url,
        body_for_hash_only=str(text_body),
        in_reply_to=in_reply_to,
        chat_id=str(chat_id),
        contact_identity_kind="telegram_id",
        contact_identity_value=sender_id,
        contact_display_name=display_name,
        contact_source_native_id=sender_id,
    )
    return await ingest(session, envelope)


async def ingest_max_update(
    session,
    *,
    connection_id: int,
    update: dict[str, Any],
    direction: str = "inbound",
) -> IngestResult | None:
    """Ingest a MAX Bot API update metadata envelope (D3: text is hashed only, never stored)."""
    msg = update.get("message") if isinstance(update.get("message"), dict) else update
    if not isinstance(msg, dict):
        return None
    body = msg.get("body") if isinstance(msg.get("body"), dict) else {}
    mid = body.get("mid") or msg.get("mid") or update.get("message_id")
    sender = msg.get("sender") if isinstance(msg.get("sender"), dict) else {}
    recipient = msg.get("recipient") if isinstance(msg.get("recipient"), dict) else {}
    chat_id = recipient.get("chat_id") or msg.get("chat_id") or update.get("chat_id")
    user_id = sender.get("user_id") or msg.get("user_id")
    if not mid or chat_id is None:
        return None
    if update.get("update_type") == "message_removed" or msg.get("is_deleted"):
        await mark_message_unavailable(
            session,
            connection_id=connection_id,
            channel="max",
            original_id=f"{chat_id}:{mid}",
        )
        return None

    display_name = sender.get("name") or sender.get("username") or str(user_id or chat_id)
    ts_raw = msg.get("timestamp")
    if isinstance(ts_raw, (int, float)):
        # MAX timestamps may be milliseconds or seconds
        ts_sec = ts_raw / 1000.0 if ts_raw > 10_000_000_000 else float(ts_raw)
        sent_at = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
    else:
        sent_at = datetime.now(timezone.utc)

    text_body = body.get("text") or ""
    envelope = IngestMessageInput(
        connection_id=connection_id,
        channel="max",
        original_id=f"{chat_id}:{mid}",
        direction=direction,
        sent_at=sent_at,
        received_at=datetime.now(timezone.utc),
        subject=recipient.get("title"),
        sender=str(user_id or chat_id),
        recipient=str(chat_id),
        participants=(str(user_id or chat_id), str(chat_id)),
        body_for_hash_only=str(text_body),
        chat_id=str(chat_id),
        contact_identity_kind="max_id" if user_id is not None else None,
        contact_identity_value=str(user_id) if user_id is not None else None,
        contact_display_name=str(display_name),
        contact_source_native_id=str(user_id) if user_id is not None else None,
    )
    return await ingest(session, envelope)


async def mark_message_unavailable(
    session,
    *,
    connection_id: int,
    channel: str,
    original_id: str,
    unavailable_at: datetime | None = None,
) -> bool:
    """Mark a message as unavailable in the source system without deleting local metadata."""
    tenant_id = _require_tenant()
    row = (
        await session.execute(
            select(UnifiedMessage).where(
                UnifiedMessage.tenant_id == tenant_id,
                UnifiedMessage.connection_id == connection_id,
                UnifiedMessage.channel == channel,
                UnifiedMessage.original_id == str(original_id).strip(),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    row.unavailable_at = unavailable_at or datetime.now(timezone.utc)
    await session.flush()
    return True


async def prune_expired_messages(
    session,
    *,
    retention_days: int = 90,
    now: datetime | None = None,
    batch_size: int = 500,
    tenant_id: int | None = None,
) -> int:
    """Expire unified message metadata rows older than workspace retention policy."""
    if tenant_id is None:
        tenant_id = _require_tenant()
    if type(retention_days) is not int or retention_days < 1:
        raise ValueError("retention_days must be a positive integer")
    if type(batch_size) is not int or not 1 <= batch_size <= 5000:
        raise ValueError("batch_size must be between 1 and 5000")
    ref_now = now or datetime.now(timezone.utc)
    if ref_now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    cutoff = ref_now - timedelta(days=retention_days)

    expired_ids = (
        select(UnifiedMessage.id)
        .where(
            UnifiedMessage.tenant_id == tenant_id,
            UnifiedMessage.received_at < cutoff,
        )
        .order_by(UnifiedMessage.id.asc())
        .limit(batch_size)
    )
    stmt = delete(UnifiedMessage).where(UnifiedMessage.id.in_(expired_ids)).execution_options(synchronize_session=False)
    exec_call = session.execute(stmt)
    if not inspect.isawaitable(exec_call):
        return 0
    result = await exec_call
    return int(getattr(result, "rowcount", 0) or 0)
