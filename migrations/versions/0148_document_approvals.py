"""P19-T04 revision-bound approvals and dedicated decision permission.

Revision ID: 0148
Revises: 0147
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0148"
down_revision = "0147"
branch_labels = None
depends_on = None


def _catalog(add):
    bind = op.get_bind()
    s = bind.dialect.identifier_preparer.quote_schema(settings.DB_SCHEMA)
    if add:
        bind.execute(
            sa.text(
                f"""INSERT INTO {s}.model_types
            (resource_key, model_name, table_name, description, is_managed, created_at, updated_at)
            VALUES ('documents.approval', 'documentapproval', 'document_approvals', 'Document approval decision', true, now(), now())"""
            )
        )
        bind.execute(
            sa.text(
                f"""INSERT INTO {s}.permissions (name, action_type, model_type_id, created_at, updated_at)
            SELECT 'Document approval: configure', CAST('CONFIGURE' AS {s}.action_type), id, now(), now()
            FROM {s}.model_types WHERE resource_key = 'documents.approval'"""
            )
        )
        bind.execute(
            sa.text(
                f"""INSERT INTO {s}.workspace_role_permission (role_id, permission_id)
            SELECT wr.id, p.id FROM {s}.workspace_roles wr
            JOIN {s}.permissions p ON p.action_type = CAST('CONFIGURE' AS {s}.action_type)
            JOIN {s}.model_types mt ON mt.id = p.model_type_id
            WHERE mt.resource_key = 'documents.approval' AND wr.codename IN ('owner','admin','manager')"""
            )
        )
    else:
        for table in ("role_permission", "workspace_role_permission"):
            bind.execute(
                sa.text(
                    f"""DELETE FROM {s}.{table} l USING {s}.permissions p, {s}.model_types m
                WHERE l.permission_id = p.id AND p.model_type_id = m.id AND m.resource_key = 'documents.approval'"""
                )
            )
        bind.execute(
            sa.text(
                f"""DELETE FROM {s}.permissions p USING {s}.model_types m
            WHERE p.model_type_id = m.id AND m.resource_key = 'documents.approval'"""
            )
        )
        bind.execute(sa.text(f"DELETE FROM {s}.model_types WHERE resource_key = 'documents.approval'"))


def upgrade():
    s = settings.DB_SCHEMA
    op.create_table(
        "document_approvals",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("revision_id", sa.Integer(), nullable=False),
        sa.Column("revision_hash", sa.String(71), nullable=False),
        sa.Column("requester_user_id", sa.Integer(), sa.ForeignKey(f"{s}.users.id", ondelete="SET NULL")),
        sa.Column("approver_user_id", sa.Integer(), sa.ForeignKey(f"{s}.users.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("comment_encrypted", sa.Text()),
        sa.Column("request_idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(71), nullable=False),
        sa.Column("decision_hash", sa.String(71)),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("pii_updated_at", sa.BigInteger(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            [f"{s}.knowledge_documents.tenant_id", f"{s}.knowledge_documents.id"],
            ondelete="CASCADE",
            name="fk_approval_document",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id", "revision_id"],
            [f"{s}.document_revisions.tenant_id", f"{s}.document_revisions.document_id", f"{s}.document_revisions.id"],
            ondelete="CASCADE",
            name="fk_approval_revision",
        ),
        sa.UniqueConstraint("tenant_id", "requester_user_id", "request_idempotency_key", name="uq_approval_request"),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','revision_requested','expired')", name="ck_approval_status"
        ),
        sa.CheckConstraint("revision_hash ~ '^sha256:[0-9a-f]{64}$'", name="ck_approval_revision_hash"),
        sa.CheckConstraint("request_hash ~ '^sha256:[0-9a-f]{64}$'", name="ck_approval_request_hash"),
        sa.CheckConstraint(
            "decision_hash IS NULL OR decision_hash ~ '^sha256:[0-9a-f]{64}$'", name="ck_approval_decision_hash"
        ),
        sa.CheckConstraint("expires_at > requested_at", name="ck_approval_expiry"),
        sa.CheckConstraint(
            "(status IN ('approved','rejected','revision_requested') AND responded_at IS NOT NULL AND decision_hash IS NOT NULL) OR (status IN ('pending','expired') AND responded_at IS NULL AND decision_hash IS NULL)",
            name="ck_approval_decision",
        ),
        schema=s,
    )
    op.create_index(
        "uq_approval_pending_document",
        "document_approvals",
        ["tenant_id", "document_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
        schema=s,
    )
    op.create_index(
        "ix_approval_inbox_expiry",
        "document_approvals",
        ["tenant_id", "approver_user_id", "status", "expires_at", "id"],
        schema=s,
    )
    q = op.get_bind().dialect.identifier_preparer.quote_schema(s)
    op.execute(
        f"CREATE TRIGGER contact_pii_marker BEFORE INSERT OR UPDATE ON {q}.document_approvals FOR EACH ROW EXECUTE FUNCTION {q}.bump_contact_pii_marker()"
    )
    _catalog(True)


def downgrade():
    s = settings.DB_SCHEMA
    bind = op.get_bind()
    q = bind.dialect.identifier_preparer.quote_schema(s)
    bind.execute(sa.text(f"LOCK TABLE {q}.document_approvals IN ACCESS EXCLUSIVE MODE"))
    if bind.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {q}.document_approvals)")):
        raise RuntimeError("Downgrade refused: document_approvals contains approval history")
    _catalog(False)
    op.drop_table("document_approvals", schema=s)
