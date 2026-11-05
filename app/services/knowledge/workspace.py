"""One authority for catalog rows, drawer metadata and chat handoff resolution."""

from urllib.parse import urlencode

from fastapi import HTTPException

from app.core.permissions import get_current_user, has_permission
from app.core.tenant_context import current_tenant_id
from app.models import DocumentRevision, KnowledgeDocument, KnowledgeSnippet, UnifiedMessage
from app.models.managers.knowledge_manager import BOUNDARIES
from app.services.knowledge.documents import STATUS_LABELS
from app.types import ActionType

CONTEXT_TYPES = {"knowledge", "document", "communication", "followup", "case"}


def evidence_dict(record, *, context_type=None, revision=None):
    kind = context_type or record.context_type
    row = {
        "id": record.id,
        "context_type": kind,
        "title": record.title,
        "source_ref": record.source_ref,
        "citation_anchor": record.citation_anchor,
        "data_boundary": record.data_boundary,
        "acl_scope": record.acl_scope,
        "revision_hash": revision.content_hash if revision else record.revision_hash,
        "excerpt": revision.body_text if revision else record.excerpt,
        "extraction_limit_note": "" if revision else record.extraction_limit_note,
    }
    # A document handoff pins an actual revision, not a mutable latest pointer.
    row["context_id"] = str(revision.id if revision else record.id)
    row["chat_url"] = "/app/chat?" + urlencode(
        {
            "context_type": kind,
            "context_id": row["context_id"],
            "boundary": record.data_boundary,
        }
    )
    if revision:
        row.update(
            version=revision.version,
            status=STATUS_LABELS[revision.status],
            approved_by=revision.approved_by,
            counterparty=record.counterparty,
            unsupported_terms_note=revision.unsupported_terms_note,
        )
    return row


async def _resolve_unified_message_context(object_id: int, boundary: str) -> dict | None:
    """Resolve an inbox UnifiedMessage for chat handoff (`context_type='communication'`, B-10)."""
    tenant_id = current_tenant_id()
    if tenant_id is None:
        return None
    user = get_current_user()
    if not has_permission(user, "comms.message", ActionType.VIEW):
        return None
    try:
        msg = await UnifiedMessage.objects.filter(
            id=object_id,
            tenant_id=tenant_id,
            is_hidden=False,
        ).first()
    except PermissionError:
        return None
    if msg is None or bool(getattr(msg, "is_hidden", False)):
        return None
    if getattr(msg, "tenant_id", None) != tenant_id:
        return None

    msg_boundary = getattr(msg, "data_boundary", None) or boundary
    if msg_boundary not in BOUNDARIES.get(boundary, ()):
        return None

    title = msg.subject_encrypted or f"{msg.channel.upper()} #{msg.id}"
    participants = " → ".join(p for p in (msg.sender_encrypted, msg.recipient_encrypted) if p)
    excerpt = f"{title} ({participants})" if participants else title
    source_ref = f"{msg.channel}:{msg.original_id}"
    citation_anchor = f"thread:{msg.thread_id or 0}#msg-{msg.id}"
    is_unavailable = getattr(msg, "unavailable_at", None) is not None
    return {
        "id": msg.id,
        "thread_id": msg.thread_id,
        "context_type": "communication",
        "source_kind": msg.channel,
        "title": title,
        "source_ref": source_ref,
        "href": None if is_unavailable else getattr(msg, "original_url", None),
        "citation_anchor": citation_anchor,
        "data_boundary": msg_boundary,
        "acl_scope": "comms.message.view",
        "revision_hash": msg.content_hash or "",
        "excerpt": excerpt,
        "snippet": excerpt,
        "extraction_limit_note": (
            "Оригинал сообщения недоступен в источнике (D24); только метаданные (D3)"
            if is_unavailable
            else "Только метаданные сообщения (D3)"
        ),
        "context_id": str(msg.id),
        "chat_url": "/app/chat?"
        + urlencode(
            {
                "context_type": "communication",
                "context_id": str(msg.id),
                "boundary": msg_boundary,
            }
        ),
    }


async def snippet_rows(context_type="knowledge", boundary="cloud", query=""):
    rows = await KnowledgeSnippet.objects.visible(boundary, context_type=context_type).order_by(
        KnowledgeSnippet.id.desc()
    )
    q = query.strip().casefold()
    return [
        evidence_dict(row)
        for row in rows
        if not q
        or q
        in " ".join(
            (
                row.title,
                row.source_ref,
                row.citation_anchor,
                row.excerpt,
                row.revision_hash,
            )
        ).casefold()
    ]


async def document_rows(boundary="cloud"):
    documents = await KnowledgeDocument.objects.visible(boundary).order_by(KnowledgeDocument.id.desc())
    if not documents:
        return []
    revisions = await DocumentRevision.objects.filter(
        DocumentRevision.document_id.in_([doc.id for doc in documents]),
        tenant_id=current_tenant_id(),
    ).order_by(DocumentRevision.version.desc())
    latest = {}
    for rev in revisions:
        latest.setdefault(rev.document_id, rev)
    return [
        evidence_dict(doc, context_type="document", revision=latest[doc.id]) for doc in documents if doc.id in latest
    ]


async def resolve_context(context_type, context_id, boundary):
    """URL/form data are selectors only; never trust client title, ref or boundary."""
    if context_type not in CONTEXT_TYPES or boundary not in {"local_only", "company", "cloud"}:
        raise HTTPException(400, "Некорректный контекст или граница данных")
    try:
        object_id = int(context_id)
        if object_id <= 0 or object_id > 2147483647:
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(404, "Контекст недоступен") from None
    if context_type == "document":
        revision = await DocumentRevision.objects.visible(boundary, id=object_id).first()
        doc = await KnowledgeDocument.objects.visible(boundary, id=revision.document_id).first() if revision else None
        row = evidence_dict(doc, context_type="document", revision=revision) if doc else None
    else:
        record = await KnowledgeSnippet.objects.visible(boundary, id=object_id, context_type=context_type).first()
        row = evidence_dict(record) if record else None
        if row is None and context_type == "communication":
            row = await _resolve_unified_message_context(object_id, boundary)
    if row is None:
        raise HTTPException(404, "Контекст недоступен")
    if row["data_boundary"] != boundary:
        raise HTTPException(403, "Граница контекста не совпадает с сохранённой")
    row.update(
        type=context_type,
        boundary=row["data_boundary"],
        ref=row["source_ref"],
        is_local_only=row["data_boundary"] == "local_only",
    )
    return row
