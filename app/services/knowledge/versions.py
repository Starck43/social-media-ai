"""Permission-scoped document history and append-only restoration (P19-T03)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Literal

from pydantic import Field, field_validator

from app.schemas.business import BusinessSchema
from app.services.knowledge.document_contracts import DocumentVersion
from app.services.knowledge.documents import (
    STATUS_DRAFT_INVALIDATED,
    DocumentConflict,
    DocumentUnavailable,
    _version_matches,
    compute_content_hash,
    document_service,
)


class RevisionOut(BusinessSchema):
    id: int
    version: int
    content_hash: str
    author_user_id: int | None
    author: str
    created_at: datetime
    change_description: str | None


class DiffPart(BusinessSchema):
    operation: Literal["equal", "delete", "insert", "replace"]
    before: str
    after: str


class RevisionDiff(BusinessSchema):
    from_revision_id: int
    to_revision_id: int
    granularity: Literal["line", "word"]
    parts: list[DiffPart]


class RestoreRequest(DocumentVersion):
    change_description: str | None = Field(default=None, max_length=2000)

    @field_validator("change_description")
    @classmethod
    def safe_description(cls, value):
        if value is not None and "\x00" in value:
            raise ValueError("invalid_text")
        return value


def diff_text(before: str, after: str, granularity: Literal["line", "word"]) -> list[DiffPart]:
    """Return plain-text changes, with bounded input and bounded diff work.

    Output is never HTML; callers must escape it. Oversized legacy revisions
    remain stored but cannot be compared through this synchronous endpoint.
    """
    if granularity not in ("line", "word") or len(before) > 200000 or len(after) > 200000:
        raise ValueError("diff_too_large_or_invalid")
    if granularity == "line":
        left, right = before.splitlines(keepends=True), after.splitlines(keepends=True)
    else:
        left, right = re.findall(r"\s+|\S+", before), re.findall(r"\s+|\S+", after)
    if len(left) > 4000 or len(right) > 4000:
        raise ValueError("diff_too_large")
    matcher = SequenceMatcher(None, left, right, autojunk=True)
    return [
        DiffPart(operation=op, before="".join(left[i:j]), after="".join(right[k:l]))
        for op, i, j, k, l in matcher.get_opcodes()
    ]


class VersionService:
    async def _by_id(self, session, doc, revision_id):
        from sqlalchemy import select

        from app.models import DocumentRevision

        if type(revision_id) is not int or revision_id <= 0:
            raise DocumentUnavailable("document_unavailable")
        revision = await session.scalar(
            select(DocumentRevision).where(
                DocumentRevision.tenant_id == doc.tenant_id,
                DocumentRevision.document_id == doc.id,
                DocumentRevision.id == revision_id,
            )
        )
        if revision is None:
            raise DocumentUnavailable("document_unavailable")
        return revision

    async def list(self, doc_id, *, boundary="cloud", limit=50, before_id=None, session=None):
        if session is None:
            from app.core.database import with_db_session

            return await with_db_session(type(self).list)(
                self, doc_id, boundary=boundary, limit=limit, before_id=before_id
            )
        from sqlalchemy import select

        from app.models import DocumentRevision
        from app.types import ActionType

        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or (before_id is not None and (type(before_id) is not int or before_id <= 0))
        ):
            raise ValueError("invalid_page")
        if boundary != "local_only":
            raise DocumentUnavailable("history_boundary_unavailable")
        identity = await document_service._actor(session, ActionType.VIEW)
        doc = await document_service._doc(session, doc_id, identity, boundary)
        query = select(DocumentRevision).where(
            DocumentRevision.tenant_id == doc.tenant_id, DocumentRevision.document_id == doc.id
        )
        if before_id is not None:
            query = query.where(DocumentRevision.id < before_id)
        rows = (await session.scalars(query.order_by(DocumentRevision.id.desc()).limit(limit + 1))).all()
        return {
            "items": [RevisionOut.model_validate(row) for row in rows[:limit]],
            "limit": limit,
            "next_before_id": rows[limit - 1].id if len(rows) > limit else None,
        }

    async def diff(self, doc_id, from_id, to_id, *, granularity="line", boundary="cloud", session=None):
        if session is None:
            from app.core.database import with_db_session

            return await with_db_session(type(self).diff)(
                self, doc_id, from_id, to_id, granularity=granularity, boundary=boundary
            )
        from app.types import ActionType

        if boundary != "local_only":
            raise DocumentUnavailable("history_boundary_unavailable")
        identity = await document_service._actor(session, ActionType.VIEW)
        doc = await document_service._doc(session, doc_id, identity, boundary)
        before = await self._by_id(session, doc, from_id)
        after = await self._by_id(session, doc, to_id)
        return RevisionDiff(
            from_revision_id=before.id,
            to_revision_id=after.id,
            granularity=granularity,
            parts=diff_text(before.body_text, after.body_text, granularity),
        )

    async def restore(self, doc_id, revision_id, data, *, boundary="cloud", session=None):
        if session is None:
            from app.core.database import with_db_session

            return await with_db_session(type(self).restore)(self, doc_id, revision_id, data, boundary=boundary)
        from app.models import DocumentRevision
        from app.types import ActionType

        data = RestoreRequest.model_validate(data.model_dump() if isinstance(data, RestoreRequest) else data)
        if boundary != "local_only":
            raise DocumentUnavailable("history_boundary_unavailable")
        identity = await document_service._actor(session, ActionType.UPDATE)
        doc = await document_service._doc(session, doc_id, identity, boundary, lock=True)
        # Historical classification is not stored per revision. Never copy old
        # content into a document currently exposed at a broader boundary.
        if doc.data_boundary != "local_only":
            raise DocumentUnavailable("history_boundary_unavailable")
        current = await document_service._revision(session, doc)
        _version_matches(doc, current, data)
        source = await self._by_id(session, doc, revision_id)
        if compute_content_hash(source.body_text) != source.content_hash:
            raise DocumentConflict("revision_hash_mismatch")
        restored = DocumentRevision(
            tenant_id=doc.tenant_id,
            document_id=doc.id,
            version=current.version + 1,
            body_text=source.body_text,
            content_hash=source.content_hash,
            status=STATUS_DRAFT_INVALIDATED,
            author="",
            author_user_id=identity[1],
            change_description=data.change_description or f"Restored revision {source.version}",
            approved_by=None,
            unsupported_terms_note=source.unsupported_terms_note,
        )
        session.add(restored)
        doc.updated_at = datetime.now(timezone.utc)
        await session.flush()
        await session.refresh(doc)
        return await document_service._output(session, doc, restored, identity)


version_service = VersionService()
