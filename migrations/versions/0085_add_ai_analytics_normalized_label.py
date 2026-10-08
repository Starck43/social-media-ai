"""Add normalized_label to ai_analytics

Revision ID: 0085
Revises: 0084
Create Date: 2026-10-08 00:00:00.000000

Adds a nullable normalized topic label for chain deduplication. The value is
lowercased, ё→е, punctuation-stripped, stemmed, and trimmed to 255 chars so
similar topic hints (e.g. "отпуск" / "отпуска" / "об отпуске") resolve to the
same chain instead of creating duplicates.
"""
from alembic import op
import sqlalchemy as sa

from app.core.config import settings

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None

schema = settings.DB_SCHEMA or "public"


def upgrade() -> None:
    op.add_column(
        "ai_analytics",
        sa.Column(
            "normalized_label",
            sa.String(length=255),
            nullable=True,
            comment="Normalized topic label for chain deduplication (lowercase, ё→е, stemmed)",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_ai_analytics_normalized_label",
        "ai_analytics",
        ["normalized_label"],
        schema=schema,
    )


def downgrade() -> None:
    op.drop_index("ix_ai_analytics_normalized_label", table_name="ai_analytics", schema=schema)
    op.drop_column("ai_analytics", "normalized_label", schema=schema)
