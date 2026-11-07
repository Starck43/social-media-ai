"""P19-T01 business document metadata, templates and revision compatibility.

Revision ID: 0142
Revises: 0141

Draft: review and real PostgreSQL migration-cycle verification are required.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import settings

revision = "0142"
down_revision = "0141"
branch_labels = None
depends_on = None

OLD_STATUSES = "'draft','approved','draft_invalidated','rejected'"
NEW_STATUSES = "'draft','in_review','approved','rejected','revision_requested','draft_invalidated'"
DOC_TYPES = "'letter','claim_response','contract','memo'"


def _replace_status_constraint(statuses):
    op.drop_constraint("ck_document_revision_status", "document_revisions", schema=settings.DB_SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_document_revision_status", "document_revisions", f"status IN ({statuses})", schema=settings.DB_SCHEMA
    )


def _revision_guard(schema, extended):
    fields = "NEW.id, NEW.tenant_id, NEW.document_id, NEW.version, NEW.body_text, NEW.content_hash, NEW.author, NEW.created_at, NEW.unsupported_terms_note"
    if extended:
        fields += ", NEW.author_user_id, NEW.change_description"
    old_fields = fields.replace("NEW.", "OLD.")
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {schema}.guard_document_revision_content() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF ROW({fields}) IS DISTINCT FROM ROW({old_fields}) THEN
                RAISE EXCEPTION 'Document revision content is immutable; append a revision'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END; $$;
    """
    )


def upgrade():
    schema = settings.DB_SCHEMA
    quoted = op.get_bind().dialect.identifier_preparer.quote_schema(schema)
    # Keep draft_invalidated stored: append still produces it, with no history rewrite.
    _replace_status_constraint(NEW_STATUSES)
    op.create_unique_constraint("uq_knowledge_snippet_tenant", "knowledge_snippets", ["tenant_id", "id"], schema=schema)
    op.create_unique_constraint(
        "uq_document_revision_document_identity",
        "document_revisions",
        ["tenant_id", "document_id", "id"],
        schema=schema,
    )
    for column in (
        sa.Column("document_type", sa.String(30)),
        sa.Column("tags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("contact_id", sa.Integer()),
        sa.Column("case_id", sa.Integer()),
        sa.Column("owner_user_id", sa.Integer()),
        sa.Column("current_revision_id", sa.Integer()),
    ):
        op.add_column("knowledge_documents", column, schema=schema)
    op.create_check_constraint(
        "ck_document_type",
        "knowledge_documents",
        f"document_type IS NULL OR document_type IN ({DOC_TYPES})",
        schema=schema,
    )
    op.create_foreign_key(
        "fk_document_contact_tenant",
        "knowledge_documents",
        "contacts",
        ["tenant_id", "contact_id"],
        ["tenant_id", "id"],
        source_schema=schema,
        referent_schema=schema,
    )
    op.create_foreign_key(
        "fk_document_case_tenant",
        "knowledge_documents",
        "knowledge_snippets",
        ["tenant_id", "case_id"],
        ["tenant_id", "id"],
        source_schema=schema,
        referent_schema=schema,
    )
    op.create_foreign_key(
        "fk_document_owner_user",
        "knowledge_documents",
        "users",
        ["owner_user_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
    )
    op.add_column("document_revisions", sa.Column("author_user_id", sa.Integer()), schema=schema)
    op.add_column("document_revisions", sa.Column("change_description", sa.Text()), schema=schema)
    op.create_foreign_key(
        "fk_revision_author_user",
        "document_revisions",
        "users",
        ["author_user_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
    )
    _revision_guard(quoted, True)
    op.execute(
        f"""
        UPDATE {quoted}.knowledge_documents AS d SET current_revision_id = (
            SELECT r.id FROM {quoted}.document_revisions r
            WHERE r.tenant_id = d.tenant_id AND r.document_id = d.id
            ORDER BY r.version DESC LIMIT 1
        )
    """
    )
    op.create_foreign_key(
        "fk_document_current_revision",
        "knowledge_documents",
        "document_revisions",
        ["tenant_id", "id", "current_revision_id"],
        ["tenant_id", "document_id", "id"],
        source_schema=schema,
        referent_schema=schema,
        deferrable=True,
        initially="DEFERRED",
    )
    # Existing manager and direct manager.create callers both advance the pointer.
    # Updating the owning row serializes concurrent inserts, and never rewinds it.
    op.execute(
        f"""
        CREATE FUNCTION {quoted}.set_document_current_revision() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE prior_id integer; prior_version integer;
        BEGIN
            SELECT d.current_revision_id INTO prior_id FROM {quoted}.knowledge_documents d
            WHERE d.tenant_id = NEW.tenant_id AND d.id = NEW.document_id FOR UPDATE;
            IF prior_id IS NOT NULL THEN
                SELECT r.version INTO prior_version FROM {quoted}.document_revisions r
                WHERE r.tenant_id = NEW.tenant_id AND r.document_id = NEW.document_id AND r.id = prior_id;
            END IF;
            IF prior_id IS NULL OR NEW.version > prior_version THEN
                UPDATE {quoted}.knowledge_documents SET current_revision_id = NEW.id
                WHERE tenant_id = NEW.tenant_id AND id = NEW.document_id;
            END IF;
            RETURN NEW;
        END; $$;
        CREATE TRIGGER document_revision_current AFTER INSERT ON {quoted}.document_revisions
            FOR EACH ROW EXECUTE FUNCTION {quoted}.set_document_current_revision();
    """
    )
    op.create_table(
        "document_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{schema}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("document_type", sa.String(30), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("variables_schema", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("tenant_id", "name", name="uq_document_template_name"),
        sa.CheckConstraint(f"document_type IN ({DOC_TYPES})", name="ck_document_template_type"),
        schema=schema,
    )
    op.create_index("ix_document_templates_tenant_id", "document_templates", ["tenant_id"], schema=schema)


def downgrade():
    bind = op.get_bind()
    schema = settings.DB_SCHEMA
    quoted = bind.dialect.identifier_preparer.quote_schema(schema)
    # Lock all changed customer tables before checking: no incompatible data can
    # appear between inspection and DDL. Refuse rather than silently lose it.
    bind.execute(
        sa.text(
            f"LOCK TABLE {quoted}.knowledge_documents, {quoted}.document_revisions, {quoted}.document_templates IN ACCESS EXCLUSIVE MODE"
        )
    )
    incompatible = bind.execute(
        sa.text(
            f"""
        SELECT EXISTS (SELECT 1 FROM {quoted}.document_templates)
            OR EXISTS (SELECT 1 FROM {quoted}.document_revisions
                       WHERE status NOT IN ({OLD_STATUSES}) OR author_user_id IS NOT NULL OR change_description IS NOT NULL)
            OR EXISTS (SELECT 1 FROM {quoted}.knowledge_documents d
                       WHERE document_type IS NOT NULL OR tags <> '[]'::jsonb OR contact_id IS NOT NULL
                          OR case_id IS NOT NULL OR owner_user_id IS NOT NULL
                          OR current_revision_id IS DISTINCT FROM (
                              SELECT r.id FROM {quoted}.document_revisions r
                              WHERE r.tenant_id=d.tenant_id AND r.document_id=d.id ORDER BY r.version DESC LIMIT 1
                          ))
    """
        )
    ).scalar_one()
    if incompatible:
        raise RuntimeError("Downgrade refused: 0142 data cannot be preserved in 0141")
    op.drop_table("document_templates", schema=schema)
    op.execute(f"DROP TRIGGER document_revision_current ON {quoted}.document_revisions")
    op.execute(f"DROP FUNCTION {quoted}.set_document_current_revision()")
    op.drop_constraint("fk_document_current_revision", "knowledge_documents", type_="foreignkey", schema=schema)
    _revision_guard(quoted, False)
    op.drop_constraint("fk_revision_author_user", "document_revisions", type_="foreignkey", schema=schema)
    for column in ("author_user_id", "change_description"):
        op.drop_column("document_revisions", column, schema=schema)
    for name in ("fk_document_owner_user", "fk_document_case_tenant", "fk_document_contact_tenant"):
        op.drop_constraint(name, "knowledge_documents", type_="foreignkey", schema=schema)
    op.drop_constraint("ck_document_type", "knowledge_documents", type_="check", schema=schema)
    for column in ("current_revision_id", "owner_user_id", "case_id", "contact_id", "tags", "document_type"):
        op.drop_column("knowledge_documents", column, schema=schema)
    op.drop_constraint("uq_document_revision_document_identity", "document_revisions", type_="unique", schema=schema)
    op.drop_constraint("uq_knowledge_snippet_tenant", "knowledge_snippets", type_="unique", schema=schema)
    _replace_status_constraint(OLD_STATUSES)
