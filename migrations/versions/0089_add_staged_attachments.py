"""Prepare nullable staged attachment snapshots (no legacy backfill).

Revision ID: 0089
Revises: 0088

Preparation is not authorization to execute DDL. Deploy only after separately
approved schema/driver acceptance; application INSERT needs this column first.
Downgrade discards the new snapshots and requires a separate rollback decision.
"""
import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "collected_items",
        sa.Column("attachments", sa.JSON(none_as_null=True), nullable=True),
        schema=settings.DB_SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("collected_items", "attachments", schema=settings.DB_SCHEMA)
