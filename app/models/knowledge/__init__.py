"""Persisted, workspace-owned source evidence and immutable document revisions."""

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.decorators import app_label
from app.models.base import Base, TenantScopedMixin, TimestampMixin


class EvidenceFields:
    title: Mapped[str] = mapped_column(String(500))
    source_ref: Mapped[str] = mapped_column(Text)
    citation_anchor: Mapped[str] = mapped_column(Text, default="")
    data_boundary: Mapped[str] = mapped_column(String(20), default="local_only")
    # Canonical permission key, checked against the active membership's grants.
    acl_scope: Mapped[str] = mapped_column(String(120), default="collection.source.view")


@app_label("knowledge")
class KnowledgeSnippet(EvidenceFields, TenantScopedMixin, TimestampMixin, Base):
    """Indexed evidence, including communication/follow-up/case source excerpts.

    These records are evidence only, not an outbox or a case execution workflow.
    """

    __tablename__ = "knowledge_snippets"
    __table_args__ = (
        Index("ix_knowledge_snippets_tenant_id", "tenant_id"),
        CheckConstraint("data_boundary IN ('local_only','company','cloud')", name="ck_knowledge_boundary"),
        CheckConstraint("context_type IN ('knowledge','communication','followup','case')", name="ck_knowledge_context"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    context_type: Mapped[str] = mapped_column(String(30), default="knowledge")
    revision_hash: Mapped[str] = mapped_column(String(80))
    excerpt: Mapped[str] = mapped_column(Text)
    extraction_limit_note: Mapped[str] = mapped_column(Text, default="")


@app_label("knowledge")
class KnowledgeDocument(EvidenceFields, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        Index("ix_knowledge_documents_tenant_id", "tenant_id"),
        UniqueConstraint("tenant_id", "id", name="uq_knowledge_document_tenant"),
        CheckConstraint("data_boundary IN ('local_only','company','cloud')", name="ck_document_boundary"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    counterparty: Mapped[str] = mapped_column(String(500), default="")


@app_label("knowledge")
class DocumentRevision(TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "document_revisions"
    __table_args__ = (
        Index("ix_document_revisions_tenant_id", "tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            [f"{settings.DB_SCHEMA}.knowledge_documents.tenant_id", f"{settings.DB_SCHEMA}.knowledge_documents.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "document_id", "version", name="uq_document_revision_version"),
        CheckConstraint(
            "content_hash = 'sha256:' || encode(sha256(convert_to(body_text, 'UTF8')), 'hex')",
            name="ck_document_revision_content_hash",
        ),
        CheckConstraint(
            "status IN ('draft','approved','draft_invalidated','rejected')", name="ck_document_revision_status"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(80))
    body_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    author: Mapped[str] = mapped_column(String(150), default="")
    approved_by: Mapped[str | None] = mapped_column(String(150), nullable=True)
    unsupported_terms_note: Mapped[str] = mapped_column(Text, default="")
