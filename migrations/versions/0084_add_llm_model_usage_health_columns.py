"""Add usage/health columns to llm_models

Counters backfill to 0 for existing rows via server_default; last_success_at
is backfilled from ai_analytics history by scripts/backfill_llm_model_success.py
(NULL stays legal — it means "unknown history", the model stays selectable).

Revision ID: 0084
Revises: 0083
Create Date: 2026-10-07
"""
from alembic import op
import sqlalchemy as sa

from app.core.config import settings

# revision identifiers, used by Alembic.
revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None

schema = settings.DB_SCHEMA or "public"


def upgrade() -> None:
    # Add columns to llm_models
    op.add_column(
        "llm_models",
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        schema=schema,
    )
    op.add_column(
        "llm_models",
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        schema=schema,
    )
    op.add_column(
        "llm_models",
        sa.Column("last_error_at", sa.DateTime(), nullable=True),
        schema=schema,
    )
    op.add_column(
        "llm_models",
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        schema=schema,
    )
    op.add_column(
        "llm_models",
        sa.Column("fail_count", sa.Integer(), nullable=False, server_default="0"),
        schema=schema,
    )


def downgrade() -> None:
    op.drop_column("llm_models", "fail_count", schema=schema)
    op.drop_column("llm_models", "use_count", schema=schema)
    op.drop_column("llm_models", "last_error_at", schema=schema)
    op.drop_column("llm_models", "last_success_at", schema=schema)
    op.drop_column("llm_models", "last_used_at", schema=schema)
