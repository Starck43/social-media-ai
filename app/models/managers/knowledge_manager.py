"""Source-right-aware reads; explicit workspace criteria survive operator bypass."""

from app.core.access_catalog import RESOURCES
from app.core.permissions import get_current_user, has_permission
from app.core.tenant_context import current_tenant_id
from app.models.managers.base_manager import BaseManager
from app.types import ActionType

# Destination clearance: local processing can read all three classes, company
# processing cannot read local-only, external/cloud processing only reads cloud.
BOUNDARIES = {"local_only": ("local_only", "company", "cloud"), "company": ("company", "cloud"), "cloud": ("cloud",)}


def permitted_scopes():
    user = get_current_user()
    if current_tenant_id() is None or not has_permission(user, "source", ActionType.VIEW):
        return ()
    return tuple(
        f"{resource.key}.{action.db_value}"
        for resource in RESOURCES
        if resource.workspace
        for action in resource.actions
        if has_permission(user, resource.model_name, action)
    )


class EvidenceManager(BaseManager):
    def visible(self, boundary="cloud", **filters):
        if boundary not in BOUNDARIES:
            raise ValueError("Unknown evidence boundary")
        scopes = permitted_scopes()
        # 'public' means workspace-readable, never anonymous/global access.
        return self.filter(
            self.model.acl_scope.in_((*scopes, "public") if scopes else ()),
            self.model.data_boundary.in_(BOUNDARIES[boundary]),
            tenant_id=current_tenant_id(),
            **filters,
        )


class DocumentRevisionManager(BaseManager):
    """Append-only content snapshots. New content never inherits approval."""

    def visible(self, boundary="cloud", **filters):
        from sqlalchemy import select

        from app.models import KnowledgeDocument

        if boundary not in BOUNDARIES:
            raise ValueError("Unknown evidence boundary")
        scopes = permitted_scopes()
        permitted_documents = select(KnowledgeDocument.id).where(
            KnowledgeDocument.tenant_id == current_tenant_id(),
            KnowledgeDocument.acl_scope.in_((*scopes, "public") if scopes else ()),
            KnowledgeDocument.data_boundary.in_(BOUNDARIES[boundary]),
        )
        return self.filter(
            self.model.document_id.in_(permitted_documents),
            tenant_id=current_tenant_id(),
            **filters,
        )

    async def append(self, document_id, body_text, *, author="", unsupported_terms_note=""):
        from sqlalchemy import select

        from app.models import KnowledgeDocument
        from app.services.knowledge.documents import compute_content_hash

        if not has_permission(get_current_user(), "source", ActionType.UPDATE):
            raise PermissionError("Missing source.update")
        # Serialize revisions per document, bind both FK columns to this workspace.
        from app.core.database import async_session_maker

        async with async_session_maker() as session, session.begin():
            doc = (
                await session.execute(
                    select(KnowledgeDocument)
                    .where(
                        KnowledgeDocument.id == document_id,
                        KnowledgeDocument.tenant_id == current_tenant_id(),
                        KnowledgeDocument.acl_scope.in_((*permitted_scopes(), "public") if permitted_scopes() else ()),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if doc is None:
                raise PermissionError("Document unavailable")
            previous = (
                await self.filter(tenant_id=current_tenant_id(), document_id=doc.id, session=session)
                .order_by(self.model.version.desc())
                .first()
            )
            return await self.create(
                session=session,
                document_id=doc.id,
                version=previous.version + 1 if previous else 1,
                body_text=body_text,
                content_hash=compute_content_hash(body_text),
                status="draft_invalidated" if previous else "draft",
                approved_by=None,
                author=author,
                unsupported_terms_note=unsupported_terms_note,
            )
