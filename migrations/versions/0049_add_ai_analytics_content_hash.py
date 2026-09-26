"""add content_hash to ai_analytics

Revision ID: 0049
Revises: 0048
Create Date: 2026-09-25 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0049"
down_revision: Union[str, Sequence[str], None] = "0048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: add content_hash column and unique index scoped to source."""
    op.add_column(
        "ai_analytics",
        sa.Column(
            "content_hash",
            sa.String(length=64),
            nullable=True,
            comment="SHA256 hex digest of the analyzed content payload for deduplication",
        ),
        schema="social_manager",
    )
    op.create_index(
        "idx_ai_analytics_source_content_hash",
        "ai_analytics",
        ["source_id", "content_hash"],
        unique=False,
        schema="social_manager",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "idx_ai_analytics_source_content_hash",
        table_name="ai_analytics",
        schema="social_manager",
    )
    op.drop_column("ai_analytics", "content_hash", schema="social_manager")
