"""Persisted, workspace-owned source evidence and immutable document revisions."""

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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
        UniqueConstraint("tenant_id", "id", name="uq_knowledge_snippet_tenant"),
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
        CheckConstraint(
            "document_type IS NULL OR document_type IN ('letter','claim_response','contract','memo')",
            name="ck_document_type",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            [f"{settings.DB_SCHEMA}.contacts.tenant_id", f"{settings.DB_SCHEMA}.contacts.id"],
            name="fk_document_contact_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            [f"{settings.DB_SCHEMA}.knowledge_snippets.tenant_id", f"{settings.DB_SCHEMA}.knowledge_snippets.id"],
            name="fk_document_case_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "id", "current_revision_id"],
            [
                f"{settings.DB_SCHEMA}.document_revisions.tenant_id",
                f"{settings.DB_SCHEMA}.document_revisions.document_id",
                f"{settings.DB_SCHEMA}.document_revisions.id",
            ],
            name="fk_document_current_revision",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    counterparty: Mapped[str] = mapped_column(String(500), default="")
    document_type: Mapped[str | None] = mapped_column(String(30))
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    contact_id: Mapped[int | None] = mapped_column(Integer)
    # Cases currently exist as tenant-scoped case evidence snippets, not a cases table.
    case_id: Mapped[int | None] = mapped_column(Integer)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{settings.DB_SCHEMA}.users.id", name="fk_document_owner_user", ondelete="SET NULL")
    )
    current_revision_id: Mapped[int | None] = mapped_column(Integer)


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
        UniqueConstraint("tenant_id", "document_id", "id", name="uq_document_revision_document_identity"),
        CheckConstraint(
            "content_hash = 'sha256:' || encode(sha256(convert_to(body_text, 'UTF8')), 'hex')",
            name="ck_document_revision_content_hash",
        ),
        CheckConstraint(
            "status IN ('draft','in_review','approved','rejected','revision_requested','draft_invalidated')",
            name="ck_document_revision_status",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(80))
    body_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    author: Mapped[str] = mapped_column(String(150), default="")
    author_user_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{settings.DB_SCHEMA}.users.id", name="fk_revision_author_user", ondelete="SET NULL")
    )
    change_description: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(String(150), nullable=True)
    unsupported_terms_note: Mapped[str] = mapped_column(Text, default="")


@app_label("knowledge")
class DocumentTemplate(TenantScopedMixin, TimestampMixin, Base):
    """A workspace-owned template; callers must check permissions on every access."""

    __tablename__ = "document_templates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_document_template_name"),
        CheckConstraint(
            "document_type IN ('letter','claim_response','contract','memo')", name="ck_document_template_type"
        ),
        Index("ix_document_templates_tenant_id", "tenant_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(500))
    document_type: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    variables_schema: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
