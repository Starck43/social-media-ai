"""Approved revision -> spool -> outbox reference, with the approval rechecked.

This is the seam the two halves of P15-T14 / P19-T05 meet at. It is deliberately
the only place that:
  * reads the current revision and its approval under locks,
  * rechecks `(revision_id, revision_hash)` against the approved pair,
  * renders and spools the bytes,
  * returns the reference the outbox persists.

Nothing here sends. Delivery is the outbox's job, and it rechecks the hash again
on read (D9a: bytes are transient, a mismatch is a recorded refusal).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select

from app.models import DocumentRevision, KnowledgeDocument
from app.models.knowledge.approval import DocumentApproval
from app.services.attachments.spool import SpoolRejected
from app.services.communications.attachments import AttachmentRef


class AttachmentStitchRefused(Exception):
    """Stable refusal; the caller records it and never falls back to a re-render."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class StitchedAttachment:
    reference: AttachmentRef
    document_id: int
    revision_id: int
    revision_hash: str


@dataclass(frozen=True)
class RenderedApproval:
    """The exact approved bytes, rendered once, plus the reference they hash to.

    Callers that must bind the reference into an outbox draft before the message
    id exists (draft -> spool -> schedule) render here and spool the *same* bytes
    afterwards via `export.spool_rendered`; re-rendering could change the hash
    (PDF timestamps) and break the approved reference.
    """

    content: bytes
    reference: AttachmentRef
    document_id: int
    revision_id: int
    revision_hash: str


def _spool_item_id(outbox_message_id: int) -> str:
    if type(outbox_message_id) is not int or outbox_message_id <= 0:
        raise AttachmentStitchRefused("invalid_outbox_item")
    return f"outbox_{outbox_message_id}"


async def _verified_approved_revision(session, *, tenant_id, document_id, revision_id, approval_id):
    """Lock and recheck approval+revision+document; return (document, revision).

    Reading the approval and the revision under the same lock is what makes the
    pair trustworthy: a revision edited between the check and the render would
    otherwise be spooled under the old approval.
    """
    if type(tenant_id) is not int or tenant_id <= 0:
        raise AttachmentStitchRefused("invalid_tenant")
    if type(document_id) is not int or document_id <= 0 or type(revision_id) is not int or revision_id <= 0:
        raise AttachmentStitchRefused("invalid_revision")
    if type(approval_id) is not int or approval_id <= 0:
        raise AttachmentStitchRefused("invalid_approval")

    approval = await session.scalar(
        select(DocumentApproval)
        .where(
            DocumentApproval.id == approval_id,
            DocumentApproval.tenant_id == tenant_id,
            DocumentApproval.document_id == document_id,
            DocumentApproval.revision_id == revision_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if approval is None:
        raise AttachmentStitchRefused("approval_unavailable")
    if approval.status != "approved":
        raise AttachmentStitchRefused("approval_not_granted")

    revision = await session.scalar(
        select(DocumentRevision)
        .where(
            DocumentRevision.id == revision_id,
            DocumentRevision.document_id == document_id,
            DocumentRevision.tenant_id == tenant_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if revision is None:
        raise AttachmentStitchRefused("revision_unavailable")
    # An edited revision is refused on the body hash, not re-approved silently.
    if revision.content_hash != approval.revision_hash or revision.status != "approved":
        raise AttachmentStitchRefused("revision_changed")
    # A stored hash the database itself guarantees, rechecked before trusting it.
    actual = "sha256:" + hashlib.sha256(revision.body_text.encode("utf-8")).hexdigest()
    if actual != revision.content_hash:
        raise AttachmentStitchRefused("revision_hash_mismatch")

    document = await session.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.tenant_id == tenant_id,
        )
    )
    if document is None:
        raise AttachmentStitchRefused("document_unavailable")
    return document, revision


async def render_approved_revision(
    session,
    *,
    tenant_id: int,
    document_id: int,
    revision_id: int,
    approval_id: int,
    format: Literal["docx", "pdf"],
) -> RenderedApproval:
    """Recheck the approval under locks and render the exact bytes once.

    Performs no spool write; the caller owns where the bytes go and must keep
    the returned reference bound to whatever it persists.
    """
    from app.services.knowledge.export import ExportError, ExportRevision, render_revision

    document, revision = await _verified_approved_revision(
        session,
        tenant_id=tenant_id,
        document_id=document_id,
        revision_id=revision_id,
        approval_id=approval_id,
    )
    try:
        rendered = render_revision(
            ExportRevision(
                revision_id=revision.id,
                content_hash=revision.content_hash,
                title=document.title,
                body_text=revision.body_text,
            ),
            format,
        )
    except ExportError as exc:
        raise AttachmentStitchRefused(str(exc)) from None
    return RenderedApproval(
        content=rendered.content,
        reference=AttachmentRef(
            sha256=hashlib.sha256(rendered.content).hexdigest(),
            size=len(rendered.content),
            mime=rendered.media_type,
        ),
        document_id=document_id,
        revision_id=revision_id,
        revision_hash=revision.content_hash,
    )


async def spool_approved_revision(
    session,
    *,
    tenant_id: int,
    document_id: int,
    revision_id: int,
    approval_id: int,
    outbox_message_id: int,
    format: Literal["docx", "pdf"],
) -> StitchedAttachment:
    """Render exactly the approved bytes into the spool and return its reference."""
    from app.services.knowledge.export import ExportError, ExportRevision, spool_revision

    item_id = _spool_item_id(outbox_message_id)
    document, revision = await _verified_approved_revision(
        session,
        tenant_id=tenant_id,
        document_id=document_id,
        revision_id=revision_id,
        approval_id=approval_id,
    )

    try:
        spooled = spool_revision(
            ExportRevision(
                revision_id=revision.id,
                content_hash=revision.content_hash,
                title=document.title,
                body_text=revision.body_text,
            ),
            format,
            tenant_id=tenant_id,
            outbox_message_id=outbox_message_id,
        )
    except (ExportError, SpoolRejected) as exc:
        raise AttachmentStitchRefused(str(exc)) from None

    return StitchedAttachment(
        reference=AttachmentRef(sha256=spooled.sha256, size=spooled.size, mime=spooled.mime),
        document_id=document_id,
        revision_id=revision_id,
        revision_hash=revision.content_hash,
    )
