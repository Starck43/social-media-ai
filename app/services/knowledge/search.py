"""Metadata-only search for workspace documents."""

from datetime import datetime

from app.services.knowledge.documents import document_service


class DocumentSearchService:
    """Search current document metadata after applying actor, workspace and boundary gates."""

    async def search(
        self,
        *,
        q: str | None = None,
        boundary: str = "cloud",
        limit: int = 50,
        before_id: int | None = None,
        document_type: str | None = None,
        status: str | None = None,
        tag: str | None = None,
        contact_id: int | None = None,
        case_evidence_id: int | None = None,
        author: str | None = None,
        revision_from: datetime | None = None,
        revision_to: datetime | None = None,
        session=None,
    ):
        if session is None:
            from app.core.database import with_db_session

            return await with_db_session(type(self).search)(
                self,
                q=q,
                boundary=boundary,
                limit=limit,
                before_id=before_id,
                document_type=document_type,
                status=status,
                tag=tag,
                contact_id=contact_id,
                case_evidence_id=case_evidence_id,
                author=author,
                revision_from=revision_from,
                revision_to=revision_to,
            )

        from sqlalchemy import String, and_, cast, func, or_, select

        from app.core.access_catalog import workspace_grants
        from app.core.permissions import get_current_user, has_permission
        from app.models import DocumentRevision, KnowledgeDocument, KnowledgeSnippet
        from app.models.managers.knowledge_manager import BOUNDARIES
        from app.types import ActionType

        identity = await document_service._actor(session, ActionType.VIEW)
        if (
            boundary not in BOUNDARIES
            or type(limit) is not int
            or not 1 <= limit <= 100
            or (before_id is not None and (type(before_id) is not int or before_id <= 0))
            or (contact_id is not None and (type(contact_id) is not int or contact_id <= 0))
            or (case_evidence_id is not None and (type(case_evidence_id) is not int or case_evidence_id <= 0))
            or any(
                value is not None and (value.tzinfo is None or value.utcoffset() is None)
                for value in (revision_from, revision_to)
            )
            or (revision_from is not None and revision_to is not None and revision_from > revision_to)
            or (q is not None and not q.strip())
            or (author is not None and not author.strip())
            or (tag is not None and not tag.strip())
        ):
            raise ValueError("invalid_search")

        can_view_contacts = ("contact", ActionType.VIEW) in workspace_grants(identity[2]) and has_permission(
            get_current_user(), "contact", ActionType.VIEW
        )
        if contact_id is not None and not can_view_contacts:
            # Do not reveal whether a matching but hidden contact link exists.
            return {"items": [], "limit": limit, "next_before_id": None}

        visible_case_boundary = or_(
            and_(
                KnowledgeDocument.data_boundary == "local_only",
                KnowledgeSnippet.data_boundary.in_(BOUNDARIES["local_only"]),
            ),
            and_(
                KnowledgeDocument.data_boundary == "company",
                KnowledgeSnippet.data_boundary.in_(BOUNDARIES["company"]),
            ),
            and_(
                KnowledgeDocument.data_boundary == "cloud",
                KnowledgeSnippet.data_boundary.in_(BOUNDARIES["cloud"]),
            ),
        )
        visible_case = (
            select(KnowledgeSnippet.id)
            .where(
                KnowledgeSnippet.tenant_id == identity[0],
                KnowledgeSnippet.id == KnowledgeDocument.case_evidence_id,
                KnowledgeSnippet.context_type == "case",
                KnowledgeSnippet.acl_scope.in_(identity[3]),
                visible_case_boundary,
            )
            .exists()
        )

        query = (
            select(KnowledgeDocument, DocumentRevision)
            .join(
                DocumentRevision,
                (DocumentRevision.id == KnowledgeDocument.current_revision_id)
                & (DocumentRevision.document_id == KnowledgeDocument.id)
                & (DocumentRevision.tenant_id == KnowledgeDocument.tenant_id),
            )
            .where(
                KnowledgeDocument.tenant_id == identity[0],
                KnowledgeDocument.acl_scope.in_(identity[3]),
                KnowledgeDocument.data_boundary.in_(BOUNDARIES[boundary]),
            )
        )
        if before_id is not None:
            query = query.where(KnowledgeDocument.id < before_id)
        if q:
            safe_q = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            needle = f"%{safe_q}%"
            case_title = (
                select(KnowledgeSnippet.title)
                .where(
                    KnowledgeSnippet.tenant_id == identity[0],
                    KnowledgeSnippet.id == KnowledgeDocument.case_evidence_id,
                    KnowledgeSnippet.context_type == "case",
                    KnowledgeSnippet.acl_scope.in_(identity[3]),
                    visible_case_boundary,
                )
                .scalar_subquery()
            )
            query = query.where(
                func.concat_ws(
                    " ",
                    KnowledgeDocument.title,
                    KnowledgeDocument.document_type,
                    DocumentRevision.status,
                    cast(KnowledgeDocument.tags, String),
                    KnowledgeDocument.counterparty,
                    DocumentRevision.author,
                    case_title,
                ).ilike(needle, escape="\\")
            )
        if document_type is not None:
            query = query.where(KnowledgeDocument.document_type == document_type)
        if status is not None:
            statuses = ("draft", "draft_invalidated") if status == "draft" else (status,)
            query = query.where(DocumentRevision.status.in_(statuses))
        if tag is not None:
            query = query.where(KnowledgeDocument.tags.contains([tag]))
        if contact_id is not None:
            query = query.where(KnowledgeDocument.contact_id == contact_id)
        if case_evidence_id is not None:
            query = query.where(KnowledgeDocument.case_evidence_id == case_evidence_id, visible_case)
        if author is not None:
            safe_author = author.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(DocumentRevision.author.ilike(f"%{safe_author}%", escape="\\"))
        if revision_from is not None:
            query = query.where(DocumentRevision.created_at >= revision_from)
        if revision_to is not None:
            query = query.where(DocumentRevision.created_at <= revision_to)

        rows = (await session.execute(query.order_by(KnowledgeDocument.id.desc()).limit(limit + 1))).all()
        items = [await document_service._output(session, doc, revision, identity) for doc, revision in rows[:limit]]
        return {
            "items": items,
            "limit": limit,
            "next_before_id": rows[limit - 1][0].id if len(rows) > limit else None,
        }


document_search_service = DocumentSearchService()
