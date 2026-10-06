"""add chain_label to ai_analytics

Revision ID: 0081
Revises: 0080
Create Date: 2026-10-06 00:00:00.000000

Adds a nullable human-readable label for topic chains so grouping by
``chain_label`` (digest "Цепочки" section, web chain list) can be done at
query time without deriving it from ``summary_data`` each time.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

# revision identifiers, used by Alembic.
revision: str = '0081'
down_revision: Union[str, Sequence[str], None] = '0080'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = settings.DB_SCHEMA


def upgrade() -> None:
    op.add_column(
        'ai_analytics',
        sa.Column(
            'chain_label',
            sa.String(length=255),
            nullable=True,
            comment='Human-readable name of the topic chain (latest analysis_title or top topic)',
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column('ai_analytics', 'chain_label', schema=SCHEMA)
