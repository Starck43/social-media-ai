"""Conservative origin selection for a future reply draft (P15-T08).

This module does not authorize, persist, schedule or send replies. The caller
must obtain fresh tenant-scoped, permission-checked rows and revalidate them
under the transaction and dispatch locks. A route selected here is not proof of
consent, approval, a verified email header, or a private bot conversation.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import getaddresses
from typing import Iterable


class ReplyUnavailable(LookupError):
    """A reply origin is missing, ambiguous, or unsupported; no PII in errors."""


@dataclass(frozen=True)
class ReplyOrigin:
    thread_id: int
    inbound_message_id: int
    contact_id: int
    connection_id: int
    identity_id: int
    channel: str = "email"


def _positive(value):
    return type(value) is int and value > 0


def select_email_origin(thread, inbound, identities: Iterable) -> ReplyOrigin:
    """Bind a specifically selected inbound email to one verified contact identity.

    `thread`, `inbound`, and `identities` must be fresh server-loaded rows, never
    request-supplied objects. A caller must decide which inbound message to reply
    to, lock/revalidate the underlying rows and enforce all authority itself.
    Ambiguous addresses, deleted originals, mismatched scope and duplicate verified
    identities fail closed. No fallback to another message, identity or channel.
    """
    from app.core.pii import PIIError
    from app.services.contacts.identity import normalized_identity

    tenant = getattr(thread, "tenant_id", None)
    contact = getattr(thread, "contact_id", None)
    connection = getattr(thread, "connection_id", None)
    if (
        not all(_positive(v) for v in (tenant, thread.id, contact, connection, inbound.id))
        or thread.channel != "email"
        or inbound.tenant_id != tenant
        or inbound.thread_id != thread.id
        or inbound.contact_id != contact
        or inbound.connection_id != connection
        or inbound.channel != "email"
        or inbound.direction != "inbound"
        or inbound.is_hidden
        or inbound.unavailable_at is not None
        or not isinstance(inbound.sender_encrypted, str)
    ):
        raise ReplyUnavailable("reply_origin_unavailable")
    try:
        addresses = getaddresses([inbound.sender_encrypted])
        if len(addresses) != 1 or not addresses[0][1]:
            raise ReplyUnavailable("reply_origin_unavailable")
        sender = normalized_identity("email", addresses[0][1])
        matches = []
        for identity in identities:
            if (
                identity.tenant_id == tenant
                and identity.contact_id == contact
                and identity.kind == "email"
                and identity.verified is True
                and identity.connection_id in (None, connection)
                and _positive(identity.id)
                and normalized_identity("email", identity.value_encrypted) == sender
            ):
                matches.append(identity)
    except (ValueError, TypeError, PIIError):
        raise ReplyUnavailable("reply_origin_unavailable") from None
    if len(matches) != 1:
        raise ReplyUnavailable("reply_origin_unavailable")
    return ReplyOrigin(thread.id, inbound.id, contact, connection, matches[0].id)


@dataclass(frozen=True, repr=False)
class ReplyHeaders:
    """Exact validated wire headers; contains private metadata and must not be logged."""

    in_reply_to: str
    references: tuple[str, ...]

    def approval_value(self) -> dict:
        """Embed this value in the existing outbox content digest, not alongside it."""
        return {"in_reply_to": self.in_reply_to, "references": list(self.references)}


def verified_email_headers(inbound, provider_message_id, provider_references) -> ReplyHeaders:
    """Check fetched headers against the persisted source hash and bound resource.

    Inputs must come from a fresh, authenticated read of the exact source message
    on its bound connection, not from an API caller or model. A hash match alone
    cannot prove that provenance. Callers must bind the result to approval and
    revalidate before dispatch; this function itself performs no I/O or approval.
    """
    from app.integrations.email.smtp_send import _MESSAGE_ID
    from app.services.communications.ingest import hash_message_id

    if (
        not isinstance(provider_message_id, str)
        or len(provider_message_id) > 998
        or _MESSAGE_ID.fullmatch(provider_message_id) is None
        or not isinstance(getattr(inbound, "external_message_id_hash", None), str)
        or hash_message_id(provider_message_id) != inbound.external_message_id_hash
        or not isinstance(provider_references, (list, tuple))
        or len(provider_references) > 31
        or (len(provider_references) == 31 and provider_message_id not in provider_references)
    ):
        raise ReplyUnavailable("reply_headers_unavailable")
    references = []
    for ref in provider_references:
        if not isinstance(ref, str) or len(ref) > 998 or _MESSAGE_ID.fullmatch(ref) is None:
            raise ReplyUnavailable("reply_headers_unavailable")
        if ref not in references:
            references.append(ref)
    if provider_message_id not in references:
        references.append(provider_message_id)
    if len(" ".join(references)) > 998:
        raise ReplyUnavailable("reply_headers_unavailable")
    return ReplyHeaders(provider_message_id, tuple(references))


def reply_exemption_candidate(message, target, thread, inbound, identities: Iterable, evidence) -> bool:
    """Check persisted structural evidence only; never authorization or approval.

    Rows must be freshly loaded and locked by the trusted outbox transaction.
    The caller must also verify current permissions, exact headers and approval,
    both at scheduling and immediately before provider I/O.
    """
    try:
        origin = select_email_origin(thread, inbound, identities)
        return bool(
            message.reply_evidence_required is True
            and message.purpose == "transactional"
            and message.tenant_id == evidence.tenant_id == thread.tenant_id == target.tenant_id
            and message.contact_id == origin.contact_id
            and evidence.outbox_message_id == message.id
            and evidence.thread_id == origin.thread_id
            and evidence.inbound_message_id == origin.inbound_message_id
            and target.message_id == message.id
            and target.channel == origin.channel
            and target.connection_id == origin.connection_id
            and target.identity_id == origin.identity_id
        )
    except (ReplyUnavailable, AttributeError):
        return False


def verified_reply_evidence(message, target, thread, inbound, identities: Iterable, evidence) -> ReplyHeaders:
    """Validate the stored wire headers and structural provenance, but not authorization.

    Only use freshly loaded, transaction-locked rows. D15 additionally requires
    current rights and a digest-bound approval at scheduling and dispatch.
    """
    import json

    if not reply_exemption_candidate(message, target, thread, inbound, identities, evidence):
        raise ReplyUnavailable("reply_evidence_unavailable")
    try:
        raw = evidence.headers_encrypted
        if not isinstance(raw, str) or len(raw) > 32768:
            raise ValueError("invalid evidence")
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or set(value) != {"version", "in_reply_to", "references"}
            or type(value["version"]) is not int
            or value["version"] != 1
        ):
            raise ValueError("invalid evidence")
        headers = verified_email_headers(inbound, value["in_reply_to"], value["references"])
        if headers.approval_value() != {"in_reply_to": value["in_reply_to"], "references": value["references"]}:
            raise ValueError("noncanonical evidence")
        return headers
    except (ValueError, TypeError, KeyError, ReplyUnavailable):
        raise ReplyUnavailable("reply_evidence_unavailable") from None


def fetch_email_reply_headers(client, inbound, allowed_folders) -> ReplyHeaders:
    """Read exact source headers via an authenticated, caller-bound IMAP client.

    The trusted caller owns tenant/permission checks, a fresh connection and
    source-row lock, and creates `client` through the pinned IMAP adapter. Neither
    the client nor folder list may come from a request. UIDVALIDITY is mandatory:
    unknown or reset epochs fail closed instead of matching a recycled UID. This
    helper only performs a bounded, read-only PEEK; it never authorizes or sends.
    """
    import imaplib
    import re
    from email import policy
    from email.parser import BytesHeaderParser

    from app.integrations.email.imap_sync import ImapSyncError, validate_folder_list

    try:
        original_id = inbound.original_id
        if not isinstance(original_id, str) or not isinstance(allowed_folders, tuple):
            raise ReplyUnavailable("reply_headers_unavailable")
        folder, uidvalidity, uid = original_id.rsplit(":", 2)
        if (
            validate_folder_list([folder]) != (folder,)
            or folder not in allowed_folders
            or not uidvalidity.isascii()
            or not uidvalidity.isdecimal()
            or not uid.isascii()
            or not uid.isdecimal()
            or len(uidvalidity) > 10
            or len(uid) > 10
            or not (0 < int(uidvalidity) <= 4294967295)
            or not (0 < int(uid) <= 4294967295)
        ):
            raise ReplyUnavailable("reply_headers_unavailable")
        status, _ = client.select(folder, readonly=True)
        responses = getattr(client, "untagged_responses", None) or {}
        epochs = responses.get("UIDVALIDITY") or responses.get(b"UIDVALIDITY")
        if status != "OK" or not epochs or not isinstance(epochs[-1], bytes):
            raise ReplyUnavailable("reply_headers_unavailable")
        epoch = epochs[-1]
        if not epoch.isdigit() or len(epoch) > 10 or int(epoch) != int(uidvalidity):
            raise ReplyUnavailable("reply_headers_unavailable")
        # The partial fetch bounds the response even when the provider has giant headers.
        spec = "(UID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID REFERENCES)]<0.16385>)"
        status, items = client.uid("FETCH", uid, spec)
        if status != "OK" or not isinstance(items, (list, tuple)):
            raise ReplyUnavailable("reply_headers_unavailable")
        results = [item for item in items if item not in (None, b")", b"")]
        if len(results) != 1 or not isinstance(results[0], tuple) or len(results[0]) != 2:
            raise ReplyUnavailable("reply_headers_unavailable")
        metadata, raw = results[0]
        if (
            not isinstance(metadata, bytes)
            or len(metadata) > 4096
            or not isinstance(raw, bytes)
            or not (0 < len(raw) <= 16384)
            or not raw.endswith(b"\r\n\r\n")
        ):
            raise ReplyUnavailable("reply_headers_unavailable")
        found = re.findall(rb"\bUID\s+(\d+)\b", metadata, flags=re.IGNORECASE)
        if len(found) != 1 or len(found[0]) > 10 or int(found[0]) != int(uid):
            raise ReplyUnavailable("reply_headers_unavailable")
        # Reject non-ASCII, controls, parser defects, duplicate headers and extra fields.
        if any(byte not in (9, 10, 13) and not 32 <= byte <= 126 for byte in raw):
            raise ReplyUnavailable("reply_headers_unavailable")
        parsed = BytesHeaderParser(policy=policy.default).parsebytes(raw)
        fields = list(parsed.raw_items())
        if (
            parsed.defects
            or parsed.get_payload()
            or any(name.lower() not in {"message-id", "references"} for name, _ in fields)
        ):
            raise ReplyUnavailable("reply_headers_unavailable")
        message_ids = [value for name, value in fields if name.lower() == "message-id"]
        refs = [value for name, value in fields if name.lower() == "references"]
        if len(message_ids) != 1 or len(refs) > 1:
            raise ReplyUnavailable("reply_headers_unavailable")

        def unfold(value):
            return re.sub(r"\r?\n[ \t]+", " ", value).strip(" \t")

        message_id = unfold(message_ids[0])
        references = unfold(refs[0]).split() if refs else []
        return verified_email_headers(inbound, message_id, references)
    except (AttributeError, ValueError, TypeError, OSError, imaplib.IMAP4.error, ImapSyncError):
        raise ReplyUnavailable("reply_headers_unavailable") from None


def _read_origin_headers(connection, secret, inbound):
    """Fetch from the server-owned account, never from a request-supplied client."""
    from app.integrations.email.imap_sync import (
        _close_imap_client,
        _open_pinned_imap_client,
        get_imap_folders,
    )
    from app.utils.crypto import decrypt_secret

    config = connection.config if isinstance(connection.config, dict) else {}
    login = config.get("account_login")
    if not isinstance(login, str) or not login:
        raise ReplyUnavailable("reply_origin_unavailable")
    client = _open_pinned_imap_client(connection.provider_id, login, decrypt_secret(secret.secret_encrypted))
    try:
        return fetch_email_reply_headers(client, inbound, get_imap_folders(connection))
    finally:
        _close_imap_client(client)


async def reply_authority(session, tenant, actor_id):
    """Re-read and lock membership and D14 grants, without bypass or cached grants."""
    from sqlalchemy import select

    from app.core.access_catalog import workspace_grants
    from app.models import TenantUser, WorkspaceRole
    from app.services.communications.outbox_store import actor_authority
    from app.types import ActionType

    await actor_authority(session, tenant, actor_id, ActionType.UPDATE)
    member = await session.scalar(
        select(TenantUser)
        .where(TenantUser.tenant_id == tenant, TenantUser.user_id == actor_id, TenantUser.is_active.is_(True))
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    role = (
        await session.scalar(
            select(WorkspaceRole.codename).where(WorkspaceRole.id == member.role_id).with_for_update(read=True)
        )
        if member is not None
        else None
    )
    grants = workspace_grants(role)
    if any((resource, ActionType.VIEW) not in grants for resource in ("contact", "unifiedmessage")):
        raise PermissionError("reply_denied")


async def create_reply_draft(session, *, thread_id, inbound_message_id, subject, body, timezone, idempotency_key):
    """Create one email reply draft and encrypted provenance, without scheduling or sending.

    Caller owns the session transaction. The sender/account, identity, source and
    wire headers are loaded from locked server-owned records, never caller input.
    A missing, changed or unsupported source is a refusal, not a generic message.
    While dispatch integration is unfinished, outbox refuses all such drafts.
    """
    import asyncio
    import json
    import re
    from datetime import datetime
    from datetime import timezone as tz

    from sqlalchemy import select

    from app.models import ContactIdentity
    from app.models.communications.message import MessageThread, UnifiedMessage
    from app.models.communications.outbox import OutboxMessage, OutboxReplyEvidence, OutboxTarget
    from app.services.communications import outbox as transitions
    from app.services.communications.outbox_contracts import DraftIn
    from app.services.communications.outbox_store import (
        OutboxConflict,
        content_digest,
        current_actor,
        digest,
        projection,
        resolve_route,
        targets_for,
    )
    from app.types import ActionType

    if not _positive(thread_id) or not _positive(inbound_message_id):
        raise ValueError("invalid_reply_source")
    if not isinstance(idempotency_key, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", idempotency_key) is None:
        raise ValueError("invalid_idempotency_key")
    tenant, actor = await current_actor(session, ActionType.UPDATE, serialize=True)
    await reply_authority(session, tenant, actor)
    thread = await session.scalar(
        select(MessageThread)
        .where(MessageThread.tenant_id == tenant, MessageThread.id == thread_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    inbound = await session.scalar(
        select(UnifiedMessage)
        .where(UnifiedMessage.tenant_id == tenant, UnifiedMessage.id == inbound_message_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if thread is None or inbound is None or not _positive(thread.contact_id):
        raise ReplyUnavailable("reply_origin_unavailable")
    identities = list(
        (
            await session.scalars(
                select(ContactIdentity)
                .where(ContactIdentity.tenant_id == tenant, ContactIdentity.contact_id == thread.contact_id)
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    origin = select_email_origin(thread, inbound, identities)
    request = DraftIn(
        contact_id=origin.contact_id,
        subject=subject,
        body=body,
        purpose="transactional",
        timezone=timezone,
        targets=[{"channel": "email", "connection_id": origin.connection_id, "identity_id": origin.identity_id}],
    )
    request_hash = digest(
        {
            "kind": "thread_reply_v1",
            "thread": thread_id,
            "inbound": inbound_message_id,
            "draft": request.model_dump(mode="json"),
        }
    )
    existing = await session.scalar(
        select(OutboxMessage)
        .where(
            OutboxMessage.tenant_id == tenant,
            OutboxMessage.author_id == actor,
            OutboxMessage.idempotency_key == idempotency_key,
        )
        .with_for_update()
    )
    if existing is not None:
        if existing.request_hash != request_hash or existing.reply_evidence_required is not True:
            raise OutboxConflict("outbox_idempotency_mismatch")
        evidence = await session.scalar(
            select(OutboxReplyEvidence)
            .where(OutboxReplyEvidence.tenant_id == tenant, OutboxReplyEvidence.outbox_message_id == existing.id)
            .with_for_update(read=True)
        )
        if evidence is None or evidence.thread_id != thread_id or evidence.inbound_message_id != inbound_message_id:
            raise OutboxConflict("reply_evidence_unavailable")
        targets = await targets_for(session, existing)
        if len(targets) != 1:
            raise OutboxConflict("reply_evidence_unavailable")
        headers = verified_reply_evidence(existing, targets[0], thread, inbound, identities, evidence)
        proof = {"thread_id": thread_id, "inbound_message_id": inbound_message_id, "headers": headers.approval_value()}
        if existing.content_hash != content_digest(existing, targets, proof):
            raise OutboxConflict("outbox_intent_mismatch")
        return projection(existing, targets)

    message = OutboxMessage(
        tenant_id=tenant,
        author_id=actor,
        contact_id=origin.contact_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        subject_encrypted=request.subject or None,
        content_encrypted=request.body,
        content_hash="",
        state="validated",
        purpose="transactional",
        reply_evidence_required=True,
        timezone=request.timezone,
    )
    target = OutboxTarget(
        tenant_id=tenant,
        channel="email",
        connection_id=origin.connection_id,
        identity_id=origin.identity_id,
        attempts=0,
        state="validated",
        idempotency_key=digest([tenant, actor, idempotency_key, 0])[7:],
    )
    recipient, credential, connection, secret = await resolve_route(
        session, message, target, datetime.now(tz.utc), consent=False
    )
    # Pinned account access is blocking; do not block the event loop. The transaction
    # retains the same locked connection and source until the evidence is persisted.
    headers = await asyncio.to_thread(_read_origin_headers, connection, secret, inbound)
    target.recipient_encrypted, target.credential_hash = recipient, credential
    target.content_hash = transitions.compute_message_hash(recipient, "email", request.subject, request.body)
    message.content_hash = content_digest(
        message,
        [target],
        {
            "thread_id": origin.thread_id,
            "inbound_message_id": origin.inbound_message_id,
            "headers": headers.approval_value(),
        },
    )
    evidence = OutboxReplyEvidence(
        tenant_id=tenant,
        thread_id=origin.thread_id,
        inbound_message_id=origin.inbound_message_id,
        headers_encrypted=json.dumps({"version": 1, **headers.approval_value()}, sort_keys=True, separators=(",", ":")),
    )
    session.add(message)
    await session.flush()
    target.message_id = evidence.outbox_message_id = message.id
    session.add(target)
    session.add(evidence)
    await session.flush()
    return projection(message, [target])


async def load_verified_reply(session, message, targets):
    """Lock and verify persisted provenance and fresh D14 authority for D15.

    This returns data for approval binding, not authorization to send. The
    outbox must still verify its approval, route snapshot and current consent
    policy at the appropriate transaction boundaries.
    """
    from sqlalchemy import select

    from app.models import ContactIdentity
    from app.models.communications.message import MessageThread, UnifiedMessage
    from app.models.communications.outbox import OutboxReplyEvidence

    if getattr(message, "reply_evidence_required", False) is not True or len(targets) != 1:
        raise ReplyUnavailable("reply_evidence_unavailable")
    await reply_authority(session, message.tenant_id, message.author_id)
    evidence = await session.scalar(
        select(OutboxReplyEvidence)
        .where(
            OutboxReplyEvidence.tenant_id == message.tenant_id,
            OutboxReplyEvidence.outbox_message_id == message.id,
        )
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if evidence is None:
        raise ReplyUnavailable("reply_evidence_unavailable")
    thread = await session.scalar(
        select(MessageThread)
        .where(MessageThread.tenant_id == message.tenant_id, MessageThread.id == evidence.thread_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    inbound = await session.scalar(
        select(UnifiedMessage)
        .where(UnifiedMessage.tenant_id == message.tenant_id, UnifiedMessage.id == evidence.inbound_message_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if thread is None or inbound is None:
        raise ReplyUnavailable("reply_evidence_unavailable")
    identities = list(
        (
            await session.scalars(
                select(ContactIdentity)
                .where(ContactIdentity.tenant_id == message.tenant_id, ContactIdentity.contact_id == message.contact_id)
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    headers = verified_reply_evidence(message, targets[0], thread, inbound, identities, evidence)
    return {
        "thread_id": evidence.thread_id,
        "inbound_message_id": evidence.inbound_message_id,
        "headers": headers.approval_value(),
    }
