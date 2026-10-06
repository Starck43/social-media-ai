"""add collected_items (raw fetched content, staged until analysis consumes it)

Collected batches used to live in a local variable for the length of one call
and were then dropped: nothing could show *what* had been collected, and the
only record of having seen an item was `ai_analytics`, which stays empty while
analysis fails — so every re-run reported the whole wall as new again.

This table is the write-ahead area between fetching and analysis. Rows are
written by every collection, before the analysis runs, and are removed once an
analysis has actually stored the matching content.

Revision ID: 0068
Revises: 0067
Create Date: 2026-10-03

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision: str = "0068"
down_revision: Union[str, None] = "0067"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "collected_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("platform", sa.String(length=50), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("media_type", sa.String(length=20), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("author", sa.JSON(), nullable=True),
        sa.Column("permalink", sa.String(length=500), nullable=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            [f"{settings.DB_SCHEMA}.tenants.id"],
            ondelete="CASCADE",
        ),
        schema=settings.DB_SCHEMA,
    )

    op.create_index(
        "ix_collected_items_tenant_id",
        "collected_items",
        ["tenant_id"],
        schema=settings.DB_SCHEMA,
    )
    op.create_index(
        "ix_collected_items_source_hash",
        "collected_items",
        ["source_id", "content_hash"],
        schema=settings.DB_SCHEMA,
    )
    op.create_index(
        "ix_collected_items_run_id",
        "collected_items",
        ["run_id"],
        schema=settings.DB_SCHEMA,
    )
    op.create_index(
        "ix_collected_items_source_published",
        "collected_items",
        ["source_id", "published_at"],
        schema=settings.DB_SCHEMA,
    )
    # A regular (non-partial) unique index: PostgreSQL treats NULLs as distinct
    # in UNIQUE indexes, so multiple rows with a NULL external_id stay legal —
    # and only a non-partial index can serve as an ON CONFLICT DO NOTHING target
    # (the partial variant is not usable by asyncpg/asyncpg's ON CONFLICT).
    op.create_index(
        "uq_collected_items_source_external",
        "collected_items",
        ["source_id", "external_id"],
        unique=True,
        schema=settings.DB_SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("collected_items", schema=settings.DB_SCHEMA)