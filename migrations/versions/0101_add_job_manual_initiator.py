"""Persist authenticated actors for manually queued jobs.

Revision ID: 0101
Revises: 0100
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0101"
down_revision = "0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("initiated_by_user_id", sa.Integer(), nullable=True),
        schema=settings.DB_SCHEMA,
    )
    op.create_foreign_key(
        "fk_jobs_initiated_by_user_id_users",
        "jobs",
        "users",
        ["initiated_by_user_id"],
        ["id"],
        source_schema=settings.DB_SCHEMA,
        referent_schema=settings.DB_SCHEMA,
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_jobs_initiated_by_user_id_users", "jobs", schema=settings.DB_SCHEMA, type_="foreignkey")
    op.drop_column("jobs", "initiated_by_user_id", schema=settings.DB_SCHEMA)
