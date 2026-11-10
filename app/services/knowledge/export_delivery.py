"""Document export and email delivery through the P15-T14 attachment seam.

Phase order for `send_document_email` (never re-render after a reference is
bound; PDF bytes are not stable across renders):

  A. authorize (`source` EXPORT), lock document+revision, require an approved
     revision-bound approval, render the exact bytes once -> `AttachmentRef`
  B. outbox `draft` binds that reference into the approved `content_hash`
  C. spool the SAME rendered bytes under `outbox_<message_id>` position 0
  D. `schedule`; dispatch later re-reads and rechecks the hash (P15-T14),
     a purged or mismatched file is a recorded refusal, never a re-render

A failure between phases leaves at most a `validated` (never approved) draft
and no scheduled send: fail closed. Retrying with the same idempotency key is
safe — the draft replays, and an occupied spool position is accepted only when
its bytes match the approved reference exactly.
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy import select

from app.models.knowledge.approval import DocumentApproval
from app.services.attachments.spool import SpoolRejected
from app.services.communications.attachment_delivery import AttachmentStitchRefused, render_approved_revision
from app.services.communications.attachments import AttachmentRejected
from app.services.communications.outbox_contracts import DraftIn, ScheduleIn
from app.services.communications.outbox_store import outbox_store
from app.services.knowledge.documents import DocumentConflict, document_service
from app.services.knowledge.export import ExportRevision, render_revision, spool_rendered
from app.services.knowledge.export_contracts import SendDocumentEmailIn
from app.types import ActionType

ATTACHMENT_POSITION = 0


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
        return
    if spooled.sha256 != rendered.reference.sha256 or spooled.size != rendered.reference.size:
        raise AttachmentStitchRefused("attachment_hash_mismatch")


async def send_document_email(document_id, request, idempotency_key, *, boundary="cloud", session=None):
    """Approved revision -> email outbox item with the rendered file attached.

    `session` (when given) covers only phase A; the outbox draft/schedule own
    their transactions and the spool write is filesystem I/O deliberately kept
    outside any DB transaction.
    """
    request = SendDocumentEmailIn.model_validate(request)
    if request.target.channel != "email":
        # Refused before anything is drafted, not at dispatch (handoff contract).
        raise AttachmentRejected("channel_cannot_attach")
    if session is not None:
        tenant_id, rendered = await _approved_render_for_send(session, document_id, request.format, boundary=boundary)
    else:
        from app.core.database import async_session_maker

        async with async_session_maker() as owned, owned.begin():
            tenant_id, rendered = await _approved_render_for_send(owned, document_id, request.format, boundary=boundary)
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
        idempotency_key,
    )
    _spool_exact_bytes(tenant_id, draft["id"], rendered)
    return await outbox_store.schedule(
        ScheduleIn(
            message_id=draft["id"],
            expected_content_hash=draft["content_hash"],
            scheduled_at=request.scheduled_at,
            ttl_seconds=request.ttl_seconds,
        )
    )
