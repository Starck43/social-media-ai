"""P17-T03 read-only 1C query history (metadata only, encrypted question).

Revision ID: p17t03_onec_history
Revises: p17t01_workspace_hosts
The task revision is a placeholder; rebase and assign the final number at merge.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "p17t03_onec_history"
down_revision = "p17t01_workspace_hosts"
branch_labels = None
depends_on = None


def upgrade():
    schema = settings.DB_SCHEMA
    op.create_table(
        "onec_query_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{schema}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey(f"{schema}.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "connection_id",
            sa.Integer(),
            sa.ForeignKey(f"{schema}.workspace_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_set", sa.String(128), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("plan_fingerprint", sa.String(64), nullable=False),
        sa.Column("question_encrypted", sa.Text(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="planned"),
        sa.Column("exported_format", sa.String(10), nullable=True),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "id", name="uq_onec_query_history_tenant"),
        sa.CheckConstraint(
            "status IN ('planned','executed','refused','exported')", name="ck_onec_query_history_status"
        ),
        sa.CheckConstraint(
            "exported_format IS NULL OR exported_format IN ('xlsx','pdf')",
            name="ck_onec_query_history_export_format",
        ),
        sa.CheckConstraint("row_count >= 0", name="ck_onec_query_history_row_count"),
        sa.CheckConstraint("duration_ms >= 0", name="ck_onec_query_history_duration"),
        schema=schema,
    )
    op.create_index(
        "ix_onec_query_history_actor",
        "onec_query_history",
        ["tenant_id", "user_id", "created_at"],
        schema=schema,
    )


def downgrade():
    schema = settings.DB_SCHEMA
    op.drop_index("ix_onec_query_history_actor", table_name="onec_query_history", schema=schema)
    op.drop_table("onec_query_history", schema=schema)
