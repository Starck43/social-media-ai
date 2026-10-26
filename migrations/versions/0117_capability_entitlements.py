"""Add workspace plan expiry and durable capability usage.

Revision ID: 0117
Revises: 0112
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0117"
down_revision = "0112"
branch_labels = None
depends_on = None
SCHEMA = settings.DB_SCHEMA


def upgrade():
    op.add_column("tenants", sa.Column("plan_expires_at", sa.DateTime(timezone=True), nullable=True), schema=SCHEMA)
    op.create_table(
        "entitlement_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("capability", sa.String(160), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("initiated_by_user_id", sa.Integer(), nullable=True),
        sa.Column("plan", sa.String(20), nullable=False),
        sa.Column("catalog_version", sa.Integer(), nullable=False),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], [f"{SCHEMA}.tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "job_id", "capability", name="uq_entitlement_usage_job_capability"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_entitlement_usage_daily", "entitlement_usage", ["tenant_id", "capability", "admitted_at"], schema=SCHEMA
    )


def downgrade():
    op.drop_table("entitlement_usage", schema=SCHEMA)
    op.drop_column("tenants", "plan_expires_at", schema=SCHEMA)
