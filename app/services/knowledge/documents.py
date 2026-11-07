"""Versioned Document Service with content hash integrity and approval invalidation (P12-T03).

Core rules:
- Document revisions are uniquely tracked by SHA256 content_hash.
- Any edit to body_text creates a new revision and resets status = 'draft_invalidated',
  approved_by = None ('edits invalidate approval').
- Product approval click is recorded with approver name/timestamp and does NOT claim qualified EDS.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"
STATUS_DRAFT_INVALIDATED = "draft_invalidated"
STATUS_REJECTED = "rejected"
STATUS_IN_REVIEW = "in_review"
STATUS_REVISION_REQUESTED = "revision_requested"

STATUS_LABELS: dict[str, str] = {
    STATUS_DRAFT: "Черновик",
    STATUS_APPROVED: "Согласовано",
    STATUS_DRAFT_INVALIDATED: "Требуется повторное согласование после правки текста",
    STATUS_REJECTED: "Отклонено",
    STATUS_IN_REVIEW: "На согласовании",
    STATUS_REVISION_REQUESTED: "Требуется доработка",
}


# This is a pure state-policy helper, not an approval or persistence operation.
# A caller must enforce tenant/actor access and atomically compare the stored
# state and revision; the hash-bound approval workflow belongs to P19-T04.
_REVIEW_TRANSITIONS = {
    STATUS_DRAFT: frozenset({STATUS_IN_REVIEW}),
    STATUS_IN_REVIEW: frozenset({STATUS_APPROVED, STATUS_REJECTED, STATUS_REVISION_REQUESTED}),
    STATUS_APPROVED: frozenset(),
    STATUS_REJECTED: frozenset(),
    STATUS_REVISION_REQUESTED: frozenset(),
}


def canonical_revision_status(status: str) -> tuple[str, bool]:
    """Map legacy invalidation without losing its meaning; reject unknown values."""
    if status == STATUS_DRAFT_INVALIDATED:
        return STATUS_DRAFT, True
    if status not in _REVIEW_TRANSITIONS:
        raise ValueError("Unknown document revision status")
    return status, False


def validate_review_transition(current: str, target: str) -> None:
    """Validate only the review graph; this does not authorize or persist a change."""
    if current not in _REVIEW_TRANSITIONS or target not in _REVIEW_TRANSITIONS[current]:
        raise ValueError("Invalid document review transition")


def compute_content_hash(body_text: str) -> str:
    """Compute the full deterministic SHA-256 digest for a revision snapshot."""
    normalized = (body_text or "").encode("utf-8")
    digest = hashlib.sha256(normalized).hexdigest()
    return f"sha256:{digest}"


def create_versioned_document(
    title: str,
    counterparty: str,
    body_text: str,
    doc_id: str | None = None,
    unsupported_terms_note: str = "",
    author: str = "",
) -> dict[str, Any]:
    """Create a new versioned document in initial draft state."""
    clean_text = str(body_text or "").strip()
    c_hash = compute_content_hash(clean_text)
    now_iso = datetime.now(timezone.utc).isoformat()
    doc_id = doc_id or f"doc-{abs(hash(title + now_iso)) % 10000}"

    return {
        "id": doc_id,
        "title": str(title),
        "counterparty": str(counterparty),
        "version": "v1.0",
        "content_hash": c_hash,
        "hash": c_hash,
        "updated_at": now_iso,
        "status": STATUS_LABELS[STATUS_DRAFT],
        "status_code": STATUS_DRAFT,
        "approved_by": None,
        "unsupported_terms_note": str(unsupported_terms_note),
        "warning": str(unsupported_terms_note),
        "body_text": clean_text,
        "revisions": [
            {
                "version": "v1.0",
                "content_hash": c_hash,
                "author": author,
                "created_at": now_iso,
            }
        ],
    }


def update_document_body(
    doc: dict[str, Any],
    new_body_text: str,
    author: str = "",
) -> dict[str, Any]:
    """Update document body_text, creating a new revision.

    CRITICAL RULE (P12-T03):
    Any edit to body_text creates a new revision and resets
    status = 'draft_invalidated', approved_by = None (edits invalidate approval).
    """
    clean_new_text = str(new_body_text or "").strip()
    new_hash = compute_content_hash(clean_new_text)

    # Compute next minor version label
    current_version = str(doc.get("version", "v1.0")).lstrip("v")
    base_ver = current_version.split("-")[0]
    parts = base_ver.split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        new_version = f"v{major}.{minor + 1}-edit"
    except (ValueError, IndexError):
        new_version = f"{doc.get('version', 'v1.0')}-edit"

    now_iso = datetime.now(timezone.utc).isoformat()

    updated = dict(doc)
    updated["version"] = new_version
    updated["body_text"] = clean_new_text
    updated["content_hash"] = new_hash
    updated["hash"] = new_hash
    updated["updated_at"] = now_iso

    # Invalidate approval immediately
    updated["status"] = STATUS_LABELS[STATUS_DRAFT_INVALIDATED]
    updated["status_code"] = STATUS_DRAFT_INVALIDATED
    updated["approved_by"] = None

    revisions = list(doc.get("revisions", []))
    revisions.append(
        {
            "version": new_version,
            "content_hash": new_hash,
            "author": author,
            "created_at": now_iso,
        }
    )
    updated["revisions"] = revisions

    return updated


def approve_document(
    doc: dict[str, Any],
    approver: str,
) -> dict[str, Any]:
    """Approve a document revision."""
    approver_name = str(approver or "").strip() or "operator"
    now_iso = datetime.now(timezone.utc).isoformat()

    updated = dict(doc)
    updated["status"] = f"Согласовано ({approver_name})"
    updated["status_code"] = STATUS_APPROVED
    updated["approved_by"] = approver_name
    updated["updated_at"] = now_iso
    return updated


def reject_document(
    doc: dict[str, Any],
    reason: str = "",
) -> dict[str, Any]:
    """Reject a document revision."""
    reason_clean = str(reason or "").strip()
    status_label = f"Отклонено: {reason_clean}" if reason_clean else STATUS_LABELS[STATUS_REJECTED]

    updated = dict(doc)
    updated["status"] = status_label
    updated["status_code"] = STATUS_REJECTED
    updated["approved_by"] = None
    updated["updated_at"] = datetime.now(timezone.utc).isoformat()
    return updated


# P19-T01 operations use lazy imports to preserve the existing pure policy helpers.
class DocumentUnavailable(LookupError):
    """Missing, inaccessible or foreign document/reference."""


class DocumentConflict(ValueError):
    """Stale revision or invalid transition."""


def _project(doc, revision):
    from app.services.knowledge.document_contracts import DocumentOut

    status, invalidated = canonical_revision_status(revision.status)
    return DocumentOut(
        id=doc.id,
        title=doc.title,
        document_type=doc.document_type,
        body_text=revision.body_text,
        counterparty=doc.counterparty,
        tags=doc.tags,
        contact_id=doc.contact_id,
        case_evidence_id=doc.case_evidence_id,
        owner_user_id=doc.owner_user_id,
        data_boundary=doc.data_boundary,
        change_description=revision.change_description,
        current_revision_id=revision.id,
        version=revision.version,
        content_hash=revision.content_hash,
        status=status,
        approval_invalidated=invalidated,
        approved_by=revision.approved_by,
        decision_actor_user_id=revision.decision_actor_user_id,
        author_user_id=revision.author_user_id,
        updated_at=doc.updated_at,
    )


def _version_matches(doc, revision, expected):
    if (
        revision is None
        or revision.id != expected.expected_revision_id
        or doc.updated_at != expected.expected_updated_at
    ):
        raise DocumentConflict("document_stale")


class DocumentService:
    """Operations use the project's managed transaction, actor locks and ACLs."""

    async def _actor(self, session, action):
        from sqlalchemy import select

        from app.core.access_catalog import RESOURCES, workspace_grants
        from app.core.permissions import get_current_user, has_permission
        from app.core.tenant_context import current_tenant_id, is_bypass
        from app.models import Tenant, TenantUser, User, WorkspaceRole
        from app.types import ActionType

        tenant, actor = current_tenant_id(), get_current_user()
        if is_bypass() or type(tenant) is not int or tenant <= 0 or actor is None or not actor.is_active:
            raise PermissionError("document_denied")
        user = await session.scalar(
            select(User.id).where(User.id == actor.id, User.is_active.is_(True)).with_for_update(read=True)
        )
        workspace = await session.scalar(
            select(Tenant.id).where(Tenant.id == tenant, Tenant.is_active.is_(True)).with_for_update(read=True)
        )
        member = await session.scalar(
            select(TenantUser)
            .where(TenantUser.tenant_id == tenant, TenantUser.user_id == actor.id, TenantUser.is_active.is_(True))
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        role = (
            None
            if member is None
            else await session.scalar(
                select(WorkspaceRole.codename).where(WorkspaceRole.id == member.role_id).with_for_update(read=True)
            )
        )
        grants = workspace_grants(role)
        if (
            user is None
            or workspace is None
            or ("source", action) not in grants
            or ("source", ActionType.VIEW) not in grants
            or not has_permission(actor, "source", action)
            or not has_permission(actor, "source", ActionType.VIEW)
        ):
            raise PermissionError("document_denied")
        scopes = tuple(
            f"{r.key}.{a.db_value}"
            for r in RESOURCES
            if r.workspace
            for a in r.actions
            if (r.model_name, a) in grants and has_permission(actor, r.model_name, a)
        )
        return tenant, actor.id, role, (*scopes, "public")

    async def _doc(self, session, doc_id, identity, boundary, *, lock=False):
        from sqlalchemy import select

        from app.models import KnowledgeDocument
        from app.models.managers.knowledge_manager import BOUNDARIES

        if boundary not in BOUNDARIES or type(doc_id) is not int or doc_id <= 0:
            raise DocumentUnavailable("document_unavailable")
        query = select(KnowledgeDocument).where(
            KnowledgeDocument.id == doc_id,
            KnowledgeDocument.tenant_id == identity[0],
            KnowledgeDocument.acl_scope.in_(identity[3]),
            KnowledgeDocument.data_boundary.in_(BOUNDARIES[boundary]),
        )
        doc = await session.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
        if doc is None:
            raise DocumentUnavailable("document_unavailable")
        return doc

    async def _revision(self, session, doc, *, lock=False):
        from sqlalchemy import select

        from app.models import DocumentRevision

        query = select(DocumentRevision).where(
            DocumentRevision.id == doc.current_revision_id,
            DocumentRevision.document_id == doc.id,
            DocumentRevision.tenant_id == doc.tenant_id,
        )
        revision = await session.scalar(
            query.with_for_update().execution_options(populate_existing=True) if lock else query
        )
        if revision is None:
            raise DocumentUnavailable("document_unavailable")
        return revision

    async def _output(self, session, doc, revision, identity):
        from sqlalchemy import select

        from app.core.access_catalog import workspace_grants
        from app.core.permissions import get_current_user, has_permission
        from app.models import KnowledgeSnippet
        from app.models.managers.knowledge_manager import BOUNDARIES
        from app.types import ActionType

        result = _project(doc, revision)
        if result.contact_id is not None and (
            ("contact", ActionType.VIEW) not in workspace_grants(identity[2])
            or not has_permission(get_current_user(), "contact", ActionType.VIEW)
        ):
            result.contact_id = None
        if result.case_evidence_id is not None:
            visible = await session.scalar(
                select(KnowledgeSnippet.id).where(
                    KnowledgeSnippet.tenant_id == identity[0],
                    KnowledgeSnippet.id == result.case_evidence_id,
                    KnowledgeSnippet.context_type == "case",
                    KnowledgeSnippet.acl_scope.in_(identity[3]),
                    KnowledgeSnippet.data_boundary.in_(BOUNDARIES[doc.data_boundary]),
                )
            )
            if visible is None:
                result.case_evidence_id = None
        return result

    async def _references(self, session, data, identity):
        from sqlalchemy import select

        from app.core.access_catalog import workspace_grants
        from app.core.permissions import get_current_user, has_permission
        from app.models import Contact, KnowledgeSnippet, TenantUser, User
        from app.models.managers.knowledge_manager import BOUNDARIES
        from app.types import ActionType

        if data.owner_user_id is not None:
            member = await session.scalar(
                select(TenantUser.id)
                .join(User, User.id == TenantUser.user_id)
                .where(
                    TenantUser.tenant_id == identity[0],
                    TenantUser.user_id == data.owner_user_id,
                    TenantUser.is_active.is_(True),
                    User.is_active.is_(True),
                )
                .with_for_update(read=True)
            )
            if member is None:
                raise DocumentUnavailable("document_unavailable")
        if data.contact_id is not None:
            if ("contact", ActionType.VIEW) not in workspace_grants(identity[2]) or not has_permission(
                get_current_user(), "contact", ActionType.VIEW
            ):
                raise PermissionError("document_contact_denied")
            contact = await session.scalar(
                select(Contact.id)
                .where(
                    Contact.tenant_id == identity[0], Contact.id == data.contact_id, Contact.merged_into_id.is_(None)
                )
                .with_for_update(read=True)
            )
            if contact is None:
                raise DocumentUnavailable("document_unavailable")
        if data.case_evidence_id is not None:
            # The resulting document is source-readable. Do not turn an evidence
            # id protected by a narrower ACL into broadly visible metadata.
            evidence = await session.scalar(
                select(KnowledgeSnippet.id)
                .where(
                    KnowledgeSnippet.tenant_id == identity[0],
                    KnowledgeSnippet.id == data.case_evidence_id,
                    KnowledgeSnippet.context_type == "case",
                    KnowledgeSnippet.acl_scope.in_(identity[3]),
                    KnowledgeSnippet.acl_scope.in_(("public", "collection.source.view")),
                    KnowledgeSnippet.data_boundary.in_(BOUNDARIES[data.data_boundary]),
                )
                .with_for_update(read=True)
            )
            if evidence is None:
                raise DocumentUnavailable("document_unavailable")

    async def create(self, data, *, session=None):
        from app.services.knowledge.document_contracts import DocumentCreate

        if session is None:
            from app.core.database import with_db_session

            return await with_db_session(type(self).create)(self, data)
        from uuid import uuid4

        from app.models import DocumentRevision, KnowledgeDocument
        from app.types import ActionType

        data = DocumentCreate.model_validate(data.model_dump() if isinstance(data, DocumentCreate) else data)
        identity = await self._actor(session, ActionType.CREATE)
        await self._references(session, data, identity)
        doc = KnowledgeDocument(
            tenant_id=identity[0],
            title=data.title,
            counterparty=data.counterparty,
            document_type=data.document_type,
            tags=data.tags,
            contact_id=data.contact_id,
            case_evidence_id=data.case_evidence_id,
            owner_user_id=data.owner_user_id or identity[1],
            data_boundary=data.data_boundary,
            source_ref=f"document:{uuid4()}",
            citation_anchor="",
            acl_scope="collection.source.view",
        )
        session.add(doc)
        await session.flush()
        revision = DocumentRevision(
            tenant_id=identity[0],
            document_id=doc.id,
            version=1,
            body_text=data.body_text,
            content_hash=compute_content_hash(data.body_text),
            status=STATUS_DRAFT,
            author="",
            author_user_id=identity[1],
            change_description=data.change_description,
            approved_by=None,
            unsupported_terms_note="",
        )
        session.add(revision)
        await session.flush()
        await session.refresh(doc)
        return await self._output(session, doc, revision, identity)

    async def get(self, doc_id, *, boundary="cloud", session=None):
        if session is None:
            from app.core.database import with_db_session

            return await with_db_session(type(self).get)(self, doc_id, boundary=boundary)
        from app.types import ActionType

        identity = await self._actor(session, ActionType.VIEW)
        doc = await self._doc(session, doc_id, identity, boundary)
        return await self._output(session, doc, await self._revision(session, doc), identity)

    async def list(self, *, boundary="cloud", limit=50, before_id=None, session=None):
        if session is None:
            from app.core.database import with_db_session

            return await with_db_session(type(self).list)(self, boundary=boundary, limit=limit, before_id=before_id)
        from sqlalchemy import select

        from app.models import KnowledgeDocument
        from app.models.managers.knowledge_manager import BOUNDARIES
        from app.types import ActionType

        identity = await self._actor(session, ActionType.VIEW)
        if (
            boundary not in BOUNDARIES
            or type(limit) is not int
            or not 1 <= limit <= 100
            or (before_id is not None and (type(before_id) is not int or before_id <= 0))
        ):
            raise ValueError("invalid_page")
        query = select(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id == identity[0],
            KnowledgeDocument.acl_scope.in_(identity[3]),
            KnowledgeDocument.data_boundary.in_(BOUNDARIES[boundary]),
        )
        if before_id is not None:
            query = query.where(KnowledgeDocument.id < before_id)
        rows = (await session.scalars(query.order_by(KnowledgeDocument.id.desc()).limit(limit + 1))).all()
        items = []
        for doc in rows[:limit]:
            try:
                items.append(await self._output(session, doc, await self._revision(session, doc), identity))
            except DocumentUnavailable:
                continue  # Legacy documents without revisions remain stored, but cannot be edited here.
        return {"items": items, "limit": limit, "next_before_id": rows[limit - 1].id if len(rows) > limit else None}

    async def update(self, doc_id, data, *, boundary="cloud", session=None):
        from app.services.knowledge.document_contracts import DocumentCreate, DocumentUpdate, StoredDocumentInput

        if session is None:
            from app.core.database import with_db_session

            return await with_db_session(type(self).update)(self, doc_id, data, boundary=boundary)
        from app.models import DocumentRevision
        from app.types import ActionType

        data = DocumentUpdate.model_validate(
            data.model_dump(exclude_unset=True) if isinstance(data, DocumentUpdate) else data
        )
        identity = await self._actor(session, ActionType.UPDATE)
        # Lock referenced rows before the document: deletion triggers hold the
        # target row first and then clear links on documents. Re-read and compare
        # the document after acquiring the references to avoid a stale snapshot.
        doc = await self._doc(session, doc_id, identity, boundary)
        revision = await self._revision(session, doc)
        _version_matches(doc, revision, data)
        changes = data.model_dump(exclude_unset=True, exclude={"expected_revision_id", "expected_updated_at"})
        base = _project(doc, revision).model_dump(include=set(DocumentCreate.model_fields))
        base.update(changes)
        updated = StoredDocumentInput.model_validate(base)
        await self._references(session, updated, identity)
        doc = await self._doc(session, doc_id, identity, boundary, lock=True)
        revision = await self._revision(session, doc)
        _version_matches(doc, revision, data)
        metadata_fields = (
            "title",
            "document_type",
            "counterparty",
            "tags",
            "contact_id",
            "case_evidence_id",
            "owner_user_id",
            "data_boundary",
        )
        metadata_changed = any(getattr(doc, key) != getattr(updated, key) for key in metadata_fields)
        body_changed = updated.body_text != revision.body_text
        for key in metadata_fields:
            setattr(doc, key, getattr(updated, key))
        # A material metadata edit also creates a new revision and cannot leave
        # a decision on the previous document state looking current. FK erasure
        # on private deletion is a separate database operation, not a user edit.
        if metadata_changed or body_changed:
            revision = DocumentRevision(
                tenant_id=identity[0],
                document_id=doc.id,
                version=revision.version + 1,
                body_text=updated.body_text,
                content_hash=compute_content_hash(updated.body_text),
                status=STATUS_DRAFT_INVALIDATED,
                author="",
                author_user_id=identity[1],
                change_description=changes.get("change_description"),
                approved_by=None,
                unsupported_terms_note=revision.unsupported_terms_note,
            )
            session.add(revision)
        elif "change_description" in changes:
            raise DocumentConflict("description_requires_new_revision")
        await session.flush()
        await session.refresh(doc)
        return await self._output(session, doc, revision, identity)

    async def transition(self, doc_id, data, *, boundary="cloud", session=None):
        from app.services.knowledge.document_contracts import DocumentTransition

        if session is None:
            from app.core.database import with_db_session

            return await with_db_session(type(self).transition)(self, doc_id, data, boundary=boundary)
        from app.types import ActionType

        data = DocumentTransition.model_validate(data.model_dump() if isinstance(data, DocumentTransition) else data)
        identity = await self._actor(session, ActionType.UPDATE)
        doc = await self._doc(session, doc_id, identity, boundary, lock=True)
        revision = await self._revision(session, doc, lock=True)
        _version_matches(doc, revision, data)
        if revision.content_hash != data.expected_hash:
            raise DocumentConflict("document_stale")
        status, _ = canonical_revision_status(revision.status)
        if status != data.expected_status:
            raise DocumentConflict("document_stale")
        try:
            validate_review_transition(status, data.target)
        except ValueError:
            raise DocumentConflict("invalid_transition") from None
        # TEMPORARY P19-T01 ONLY: delete this rule when P19-T04 introduces its
        # assigned approver and dedicated permission; never extend/inherit it.
        # _actor locks and re-reads the active user, workspace, membership and
        # workspace role in this same transaction, before the document lock.
        if data.target in {STATUS_APPROVED, STATUS_REJECTED, STATUS_REVISION_REQUESTED}:
            if identity[2] != "owner":
                raise PermissionError("document_decision_denied")
            revision.decision_actor_user_id = identity[1]
            revision.approved_by = f"user:{identity[1]}" if data.target == STATUS_APPROVED else None
        revision.status = data.target
        doc.updated_at = datetime.now(timezone.utc)
        await session.flush()
        await session.refresh(doc)
        return await self._output(session, doc, revision, identity)

    async def delete(self, doc_id, data, *, boundary="cloud", session=None):
        from app.services.knowledge.document_contracts import DocumentVersion

        if session is None:
            from app.core.database import with_db_session

            return await with_db_session(type(self).delete)(self, doc_id, data, boundary=boundary)
        from app.types import ActionType

        data = DocumentVersion.model_validate(data.model_dump() if isinstance(data, DocumentVersion) else data)
        identity = await self._actor(session, ActionType.DELETE)
        doc = await self._doc(session, doc_id, identity, boundary, lock=True)
        _version_matches(doc, await self._revision(session, doc), data)
        await session.delete(doc)
        await session.flush()
        return {"deleted": True}

    async def _template(self, session, template_id, identity, *, lock=False):
        from sqlalchemy import select

        from app.models.knowledge import DocumentTemplate

        if type(template_id) is not int or template_id <= 0:
            raise DocumentUnavailable("template_unavailable")
        query = select(DocumentTemplate).where(
            DocumentTemplate.tenant_id == identity[0], DocumentTemplate.id == template_id
        )
        template = await session.scalar(query.with_for_update() if lock else query)
        if template is None:
            raise DocumentUnavailable("template_unavailable")
        return template

    async def list_templates(self, *, limit=50, before_id=None, session=None):
        from app.services.knowledge.document_contracts import TemplateOut

        if session is None:
            from app.core.database import with_db_session

            return await with_db_session(type(self).list_templates)(self, limit=limit, before_id=before_id)
        from sqlalchemy import select

        from app.models.knowledge import DocumentTemplate
        from app.types import ActionType

        identity = await self._actor(session, ActionType.VIEW)
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or (before_id is not None and (type(before_id) is not int or before_id <= 0))
        ):
            raise ValueError("invalid_page")
        query = select(DocumentTemplate).where(DocumentTemplate.tenant_id == identity[0])
        if before_id is not None:
            query = query.where(DocumentTemplate.id < before_id)
        rows = (await session.scalars(query.order_by(DocumentTemplate.id.desc()).limit(limit + 1))).all()
        return {
            "items": [TemplateOut.model_validate(row) for row in rows[:limit]],
            "limit": limit,
            "next_before_id": rows[limit - 1].id if len(rows) > limit else None,
        }

    async def get_template(self, template_id, *, session=None):
        from app.services.knowledge.document_contracts import TemplateOut

        if session is None:
            from app.core.database import with_db_session

            return await with_db_session(type(self).get_template)(self, template_id)
        from app.types import ActionType

        identity = await self._actor(session, ActionType.VIEW)
        return TemplateOut.model_validate(await self._template(session, template_id, identity))

    async def create_template(self, data, *, session=None):
        from app.services.knowledge.document_contracts import TemplateCreate, TemplateOut

        if session is None:
            from app.core.database import with_db_session

            return await with_db_session(type(self).create_template)(self, data)
        from sqlalchemy.exc import IntegrityError

        from app.models.knowledge import DocumentTemplate
        from app.types import ActionType

        data = TemplateCreate.model_validate(data.model_dump() if isinstance(data, TemplateCreate) else data)
        identity = await self._actor(session, ActionType.CREATE)
        template = DocumentTemplate(tenant_id=identity[0], **data.model_dump())
        session.add(template)
        try:
            await session.flush()
        except IntegrityError:
            raise DocumentConflict("template_conflict") from None
        return TemplateOut.model_validate(template)

    async def update_template(self, template_id, data, *, session=None):
        from app.services.knowledge.document_contracts import TemplateOut, TemplateUpdate

        if session is None:
            from app.core.database import with_db_session

            return await with_db_session(type(self).update_template)(self, template_id, data)
        from sqlalchemy.exc import IntegrityError

        from app.types import ActionType

        data = TemplateUpdate.model_validate(data.model_dump() if isinstance(data, TemplateUpdate) else data)
        identity = await self._actor(session, ActionType.UPDATE)
        template = await self._template(session, template_id, identity, lock=True)
        if template.updated_at != data.expected_updated_at:
            raise DocumentConflict("template_stale")
        for key, value in data.model_dump(exclude={"expected_updated_at"}).items():
            setattr(template, key, value)
        try:
            await session.flush()
        except IntegrityError:
            raise DocumentConflict("template_conflict") from None
        await session.refresh(template)
        return TemplateOut.model_validate(template)

    async def delete_template(self, template_id, data, *, session=None):
        from app.services.knowledge.document_contracts import TemplateVersion

        if session is None:
            from app.core.database import with_db_session

            return await with_db_session(type(self).delete_template)(self, template_id, data)
        from app.types import ActionType

        data = TemplateVersion.model_validate(data.model_dump() if isinstance(data, TemplateVersion) else data)
        identity = await self._actor(session, ActionType.DELETE)
        template = await self._template(session, template_id, identity, lock=True)
        if template.updated_at != data.expected_updated_at:
            raise DocumentConflict("template_stale")
        await session.delete(template)
        await session.flush()
        return {"deleted": True}


document_service = DocumentService()
