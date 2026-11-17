"""Persist workspace evidence and document revisions; no demo/backfill records.

Revision ID: 0122
Revises: 0121
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0122"
down_revision = "0121"
branch_labels = None
depends_on = None


def _common():
    schema = settings.DB_SCHEMA
    return [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{schema}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def _evidence():
    return [
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("citation_anchor", sa.Text(), nullable=False),
        sa.Column("data_boundary", sa.String(20), nullable=False),
        sa.Column("acl_scope", sa.String(120), nullable=False),
    ]


def upgrade():
    schema = settings.DB_SCHEMA
    op.create_table(
        "knowledge_snippets",
        *_common(),
        *_evidence(),
        sa.Column("context_type", sa.String(30), nullable=False),
        sa.Column("revision_hash", sa.String(80), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("extraction_limit_note", sa.Text(), nullable=False),
        sa.CheckConstraint("data_boundary IN ('local_only','company','cloud')", name="ck_knowledge_boundary"),
        sa.CheckConstraint(
            "context_type IN ('knowledge','communication','followup','case')", name="ck_knowledge_context"
        ),
        schema=schema,
    )
    op.create_table(
        "knowledge_documents",
        *_common(),
        *_evidence(),
        sa.Column("counterparty", sa.String(500), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_knowledge_document_tenant"),
        sa.CheckConstraint("data_boundary IN ('local_only','company','cloud')", name="ck_document_boundary"),
        schema=schema,
    )
    op.create_table(
        "document_revisions",
        *_common(),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(80), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("author", sa.String(150), nullable=False),
        sa.Column("approved_by", sa.String(150), nullable=True),
        sa.Column("unsupported_terms_note", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            [f"{schema}.knowledge_documents.tenant_id", f"{schema}.knowledge_documents.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "document_id", "version", name="uq_document_revision_version"),
        sa.CheckConstraint(
            "content_hash = 'sha256:' || encode(sha256(convert_to(body_text, 'UTF8')), 'hex')",
            name="ck_document_revision_content_hash",
        ),
        sa.CheckConstraint(
            "status IN ('draft','approved','draft_invalidated','rejected')", name="ck_document_revision_status"
        ),
        schema=schema,
    )
    op.execute(
        f"""
        CREATE FUNCTION "{schema}".guard_document_revision_content() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF ROW(NEW.id, NEW.tenant_id, NEW.document_id, NEW.version, NEW.body_text,
                   NEW.content_hash, NEW.author, NEW.created_at, NEW.unsupported_terms_note)
               IS DISTINCT FROM
               ROW(OLD.id, OLD.tenant_id, OLD.document_id, OLD.version, OLD.body_text,
                   OLD.content_hash, OLD.author, OLD.created_at, OLD.unsupported_terms_note) THEN
                RAISE EXCEPTION 'Document revision content is immutable; append a revision'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END; $$;
        CREATE TRIGGER document_revision_content_immutable
            BEFORE UPDATE ON "{schema}".document_revisions
            FOR EACH ROW EXECUTE FUNCTION "{schema}".guard_document_revision_content();
    """
    )
    for table in ("knowledge_snippets", "knowledge_documents", "document_revisions"):
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"], schema=schema)


def downgrade():
    # Destructive only for these new records. Backup before any approved rollback.
    for table in ("document_revisions", "knowledge_documents", "knowledge_snippets"):
        op.drop_table(table, schema=settings.DB_SCHEMA)
    op.execute(f'DROP FUNCTION "{settings.DB_SCHEMA}".guard_document_revision_content()')
