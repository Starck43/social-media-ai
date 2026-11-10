"""P19-T05 document export and email delivery endpoints.

Export renders the current revision for an EXPORT-authorized caller and returns
a hash-evidenced base64 envelope. Send/email stitches the approved revision
through the P15-T14 attachment seam into a scheduled outbox item; the outbox
owns delivery, tracking and every dispatch-time recheck.
"""

import base64
import hashlib
from typing import Literal

from fastapi import Depends, HTTPException, Path

from app.api.deps import require_model_perm
from app.api.v1.conventions import business_router, require_idempotency_key, validate_business_routes
from app.services.attachments.spool import SpoolRejected
from app.services.communications.attachment_delivery import AttachmentStitchRefused
from app.services.communications.attachments import AttachmentRejected
from app.services.communications.outbox import CommunicationError
from app.services.communications.outbox_contracts import MessageOut
from app.services.communications.outbox_store import OutboxConflict, OutboxUnavailable
from app.services.knowledge.documents import DocumentConflict, DocumentUnavailable
from app.services.knowledge.export_contracts import ExportFileOut, ExportRequestIn, SendDocumentEmailIn
from app.services.knowledge.export_delivery import export_document, send_document_email
from app.types import ActionType

router = business_router("documents", permission=require_model_perm("source", ActionType.EXPORT))


async def invoke(operation):
    try:
        return await operation
    except DocumentUnavailable:
        raise HTTPException(404) from None
    except OutboxUnavailable:
        raise HTTPException(404) from None
    except (DocumentConflict, OutboxConflict, CommunicationError):
        raise HTTPException(409) from None
    except (AttachmentStitchRefused, AttachmentRejected, SpoolRejected):
        # Recorded refusals with stable codes; never a partial or silent retry.
        raise HTTPException(409) from None
    except PermissionError:
        raise HTTPException(403) from None
    except ValueError:
        raise HTTPException(422) from None


@router.post("/{document_id}/export/{format}", response_model=ExportFileOut)
async def export_document_file(
    document_id: int = Path(gt=0),
    format: Literal["docx", "pdf"] = Path(),
    body: ExportRequestIn | None = None,
):
    guard = body or ExportRequestIn()
    exported = await invoke(
        export_document(
            document_id,
            format,
            expected_revision_id=guard.expected_revision_id,
            expected_content_hash=guard.expected_content_hash,
        )
    )
    return ExportFileOut(
        filename=f"document-{document_id}-r{exported.revision_id}.{format}",
        media_type=exported.media_type,
        size=len(exported.content),
        sha256=hashlib.sha256(exported.content).hexdigest(),
        revision_id=exported.revision_id,
        revision_body_hash=exported.content_hash,
        content_b64=base64.b64encode(exported.content).decode("ascii"),
    )


@router.post(
    "/{document_id}/send/email",
    response_model=MessageOut,
    status_code=201,
    openapi_extra={"x-creates-command": True},
    dependencies=[Depends(require_model_perm("comms.outbox", ActionType.CONFIGURE))],
)
async def send_document_via_email(
    body: SendDocumentEmailIn,
    document_id: int = Path(gt=0),
    idempotency_key: str = Depends(require_idempotency_key),
):
    """Draft+schedule one email carrying the approved revision; delivery is the outbox's."""
    return await invoke(send_document_email(document_id, body, idempotency_key))


validate_business_routes(router)
