"""Document export and email delivery through the P15-T14 attachment seam.

Phase order for a new `send_document_email` (never re-render after a reference
is bound; PDF bytes are not stable across renders):

  A. authorize (`source` EXPORT), lock document+revision, require an approved
     revision-bound approval, render the exact bytes once -> `AttachmentRef`
  B. outbox `draft` binds that reference into the approved `content_hash`
  C. spool the SAME rendered bytes under `outbox_<message_id>` position 0
  D. `schedule`; dispatch later re-reads and rechecks the hash (P15-T14),
     a purged or mismatched file is a recorded refusal, never a re-render

Draft, spool, schedule and provenance commit in one database transaction. A
retry uses the persisted attachment reference and provenance, so it never
re-renders or touches the spool. Any failure before commit rolls back the draft
and removes only the spool item written by that attempt.
"""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from sqlalchemy import select

from app.models.actions.business_command import BusinessCommand
from app.models.communications.outbox import OutboxAttachmentRef, OutboxMessage
from app.models.knowledge.approval import DocumentApproval
from app.services.attachments.spool import SpoolRejected
from app.services.communications.attachment_delivery import AttachmentStitchRefused, render_approved_revision
from app.services.communications.attachments import AttachmentRef, AttachmentRejected
from app.services.communications.outbox_contracts import DraftIn, ScheduleIn
from app.services.communications.outbox_store import current_actor, outbox_store
from app.services.knowledge.documents import DocumentConflict, document_service
from app.services.knowledge.export import ExportRevision, render_revision, spool_rendered
from app.services.knowledge.export_contracts import SendDocumentEmailIn
from app.types import ActionType

ATTACHMENT_POSITION = 0
_DELIVERY_METADATA_KEY = "document_export"


def _outbox_idempotency_key(document_id: int, key: str) -> str:
    """Scope a caller key to this resource and endpoint without storing it elsewhere."""
    if not isinstance(key, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", key) is None:
        raise ValueError("invalid_idempotency_key")
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"p19t05:{document_id}:{digest}"


def _metadata(*, document_id, revision_id, revision_hash, format, reference, scheduled_at, ttl_seconds):
    data = {
        "document_id": document_id,
        "revision_id": revision_id,
        "revision_body_hash": revision_hash,
        "format": format,
        "scheduled_at": scheduled_at.isoformat(),
        "ttl_seconds": ttl_seconds,
    }
    if reference is not None:
        data.update(
            attachment_sha256=reference.sha256,
            attachment_size=reference.size,
            attachment_mime=reference.mime,
        )
    return data


def _same_metadata(existing, expected):
    return isinstance(existing, dict) and all(existing.get(key) == value for key, value in expected.items())


async def _locked_current(session, document_id, *, boundary):
    """EXPORT-authorized actor plus the locked document and its current revision."""
    identity = await document_service._actor(session, ActionType.EXPORT)
    doc = await document_service._doc(session, document_id, identity, boundary, lock=True)
    revision = await document_service._revision(session, doc, lock=True)
    return identity, doc, revision


async def export_document(
    document_id,
    format: Literal["docx", "pdf"],
    *,
    expected_revision_id=None,
    expected_content_hash=None,
    boundary="cloud",
    session=None,
):
    """Render the current revision for an EXPORT-authorized caller; no delivery.

    The optional staleness guard refuses with `document_stale` when the current
    revision moved past what the caller saw.
    """
    if session is None:
        from app.core.database import async_session_maker

        async with async_session_maker() as owned, owned.begin():
            return await export_document(
                document_id,
                format,
                expected_revision_id=expected_revision_id,
                expected_content_hash=expected_content_hash,
                boundary=boundary,
                session=owned,
            )
    _, doc, revision = await _locked_current(session, document_id, boundary=boundary)
    if expected_revision_id is not None and revision.id != expected_revision_id:
        raise DocumentConflict("document_stale")
    if expected_content_hash is not None and revision.content_hash != expected_content_hash:
        raise DocumentConflict("document_stale")
    return render_revision(
        ExportRevision(
            revision_id=revision.id,
            content_hash=revision.content_hash,
            title=doc.title,
            body_text=revision.body_text,
        ),
        format,
    )


async def _approved_render_for_send(session, document_id, format, *, boundary):
    """Phase A: locked approval recheck through the seam, rendered exactly once."""
    identity, doc, revision = await _locked_current(session, document_id, boundary=boundary)
    approval = await session.scalar(
        select(DocumentApproval)
        .where(
            DocumentApproval.tenant_id == identity[0],
            DocumentApproval.document_id == doc.id,
            DocumentApproval.revision_id == revision.id,
            DocumentApproval.status == "approved",
        )
        .order_by(DocumentApproval.id.desc())
        .limit(1)
    )
    if approval is None:
        raise DocumentConflict("approval_not_granted")
    rendered = await render_approved_revision(
        session,
        tenant_id=identity[0],
        document_id=doc.id,
        revision_id=revision.id,
        approval_id=approval.id,
        format=format,
    )
    return identity[0], rendered


def _spool_exact_bytes(tenant_id, outbox_message_id, rendered):
    """Phase C: spool the already-rendered bytes; never render a second time."""
    from app.services.attachments import attachment_spool

    try:
        spooled = spool_rendered(
            rendered.content,
            rendered.reference.mime,
            tenant_id=tenant_id,
            outbox_message_id=outbox_message_id,
            position=ATTACHMENT_POSITION,
            revision_id=rendered.revision_id,
            revision_body_hash=rendered.revision_hash,
        )
    except SpoolRejected as exc:
        if str(exc) != "attachment_position_occupied":
            raise
        # Idempotent retry after a failed schedule: accept the occupied position
        # only when it already holds exactly the approved bytes.
        try:
            attachment_spool.read(
                tenant_id, f"outbox_{outbox_message_id}", ATTACHMENT_POSITION, expected_sha256=rendered.reference.sha256
            )
        except SpoolRejected as read_exc:
            raise AttachmentStitchRefused(f"attachment_position_conflict:{read_exc}") from None
        return False
    if spooled.sha256 != rendered.reference.sha256 or spooled.size != rendered.reference.size:
        raise AttachmentStitchRefused("attachment_hash_mismatch")
    return True


async def send_document_email(document_id, request, idempotency_key, *, boundary="cloud", session=None):
    """Approved revision -> email outbox item with the rendered file attached.

    Draft, spool, schedule and provenance are one transaction. `session`, when
    supplied, is caller-owned; otherwise this service commits the transaction.
    """
    request = SendDocumentEmailIn.model_validate(request)
    if request.target.channel != "email":
        # Refused before anything is drafted, not at dispatch (handoff contract).
        raise AttachmentRejected("channel_cannot_attach")
    from app.core.database import async_session_maker

    internal_key = _outbox_idempotency_key(document_id, idempotency_key)
    created_spool = None

    async def run(owned):
        nonlocal created_spool
        # Take the exclusive workspace serialization lock before document locks;
        # draft() takes the same lock and must not upgrade from a shared lock.
        tenant_id, actor_id = await current_actor(owned, ActionType.CONFIGURE, serialize=True)
        identity, doc, revision = await _locked_current(owned, document_id, boundary=boundary)
        if identity[0] != tenant_id:
            raise PermissionError("outbox_denied")

        metadata = _metadata(
            document_id=doc.id,
            revision_id=revision.id,
            revision_hash=revision.content_hash,
            format=request.format,
            # Existing rows are validated against their persisted reference below.
            reference=None,
            scheduled_at=request.scheduled_at,
            ttl_seconds=request.ttl_seconds,
        )
        existing = await owned.scalar(
            select(OutboxMessage).where(
                OutboxMessage.tenant_id == tenant_id,
                OutboxMessage.author_id == actor_id,
                OutboxMessage.idempotency_key == internal_key,
            )
        )
        if existing is not None:
            refs = list(
                (
                    await owned.scalars(
                        select(OutboxAttachmentRef)
                        .where(
                            OutboxAttachmentRef.tenant_id == tenant_id,
                            OutboxAttachmentRef.outbox_message_id == existing.id,
                        )
                        .order_by(OutboxAttachmentRef.position)
                    )
                ).all()
            )
            if len(refs) != 1 or refs[0].position != ATTACHMENT_POSITION:
                raise DocumentConflict("delivery_metadata_unavailable")
            reference = AttachmentRef(sha256=refs[0].sha256, size=refs[0].size, mime=refs[0].mime)
            metadata.update(
                attachment_sha256=reference.sha256,
                attachment_size=reference.size,
                attachment_mime=reference.mime,
            )
            draft = await outbox_store.draft(
                DraftIn(
                    contact_id=request.contact_id,
                    subject=request.subject,
                    body=request.body,
                    purpose=request.purpose,
                    timezone=request.timezone,
                    targets=[request.target],
                    attachments=[reference],
                ),
                internal_key,
                session=owned,
            )
            command_ids = [target["command_id"] for target in draft["targets"] if target.get("command_id")]
            if len(command_ids) != 1:
                raise DocumentConflict("delivery_metadata_unavailable")
            command = await owned.scalar(
                select(BusinessCommand).where(
                    BusinessCommand.tenant_id == tenant_id,
                    BusinessCommand.id == command_ids[0],
                )
            )
            saved = (command.preview_snapshot or {}).get(_DELIVERY_METADATA_KEY) if command else None
            if (
                command is None
                or command.payload.get("outbox_id") != existing.id
                or command.payload.get("intent_hash") != existing.approved_hash
                or not _same_metadata(saved, metadata)
            ):
                raise DocumentConflict("delivery_idempotency_mismatch")
            if existing.state in {"draft", "validated"}:
                # New sends commit draft+spool+schedule atomically; a durable
                # validated row is legacy/partial and cannot safely be repaired.
                raise DocumentConflict("delivery_incomplete")
            return draft

        tenant_id, rendered = await _approved_render_for_send(owned, document_id, request.format, boundary=boundary)
        metadata = _metadata(
            document_id=doc.id,
            revision_id=rendered.revision_id,
            revision_hash=rendered.revision_hash,
            format=request.format,
            reference=rendered.reference,
            scheduled_at=request.scheduled_at,
            ttl_seconds=request.ttl_seconds,
        )
        draft = await outbox_store.draft(
            DraftIn(
                contact_id=request.contact_id,
                subject=request.subject,
                body=request.body,
                purpose=request.purpose,
                timezone=request.timezone,
                targets=[request.target],
                attachments=[rendered.reference],
            ),
            internal_key,
            session=owned,
        )
        created_spool = (tenant_id, f"outbox_{draft['id']}")
        if not _spool_exact_bytes(tenant_id, draft["id"], rendered):
            created_spool = None
        scheduled = await outbox_store.schedule(
            ScheduleIn(
                message_id=draft["id"],
                expected_content_hash=draft["content_hash"],
                scheduled_at=request.scheduled_at,
                ttl_seconds=request.ttl_seconds,
            ),
            session=owned,
        )
        target_commands = [target["command_id"] for target in scheduled["targets"] if target.get("command_id")]
        if len(target_commands) != 1:
            raise DocumentConflict("delivery_metadata_unavailable")
        command = await owned.scalar(
            select(BusinessCommand)
            .where(BusinessCommand.tenant_id == tenant_id, BusinessCommand.id == target_commands[0])
            .with_for_update()
        )
        message = await owned.scalar(
            select(OutboxMessage)
            .where(OutboxMessage.tenant_id == tenant_id, OutboxMessage.id == draft["id"])
            .with_for_update()
        )
        if (
            command is None
            or message is None
            or command.payload.get("outbox_id") != message.id
            or command.payload.get("intent_hash") != message.approved_hash
        ):
            raise DocumentConflict("delivery_metadata_unavailable")
        snapshot = dict(command.preview_snapshot or {})
        snapshot[_DELIVERY_METADATA_KEY] = metadata
        command.preview_snapshot = snapshot
        await owned.flush()
        return scheduled

    try:
        if session is not None:
            return await run(session)
        async with async_session_maker() as owned, owned.begin():
            result = await run(owned)
        return result
    except Exception:
        if created_spool is not None:
            from app.services.attachments import attachment_spool

            attachment_spool.discard(*created_spool)
        raise
