"""Tenant-scoped provider-account token buckets.

Revision ID: 0131
Revises: 0130
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0131"
down_revision = "0130"
branch_labels = None
depends_on = None


def upgrade():
    schema = settings.DB_SCHEMA
    op.create_table(
        "provider_rate_budgets",
        sa.Column(
            "tenant_id", sa.Integer(), sa.ForeignKey(f"{schema}.tenants.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("provider", sa.String(30), primary_key=True),
        sa.Column("account_key", sa.String(64), primary_key=True),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("tokens", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            [f"{schema}.workspace_connections.tenant_id", f"{schema}.workspace_connections.id"],
            ondelete="CASCADE",
            name="fk_rate_budget_tenant_connection",
        ),
        sa.CheckConstraint("tokens >= 0", name="ck_provider_rate_tokens_nonnegative"),
        sa.CheckConstraint("length(account_key) = 64", name="ck_provider_rate_account_digest"),
        schema=schema,
    )


def downgrade():
    op.drop_table("provider_rate_budgets", schema=settings.DB_SCHEMA)
