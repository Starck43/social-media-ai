"""drop unused date_from and date_to from sources

Revision ID: 0079
Revises: 0078
Create Date: 2026-10-05 20:30:00.000000

These columns were added in 0035 but never used by collection/analysis logic.
Date filtering is handled via AgentTask.payload["cli_dates"] instead.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.core.config import settings

# revision identifiers, used by Alembic.
revision: str = '0079'
down_revision: Union[str, Sequence[str], None] = '0078'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop unused date_from and date_to columns from sources."""
    op.drop_column('sources', 'date_to', schema=settings.DB_SCHEMA)
    op.drop_column('sources', 'date_from', schema=settings.DB_SCHEMA)


def downgrade() -> None:
    """Re-add date_from and date_to columns to sources."""
    op.add_column(
        'sources',
        sa.Column(
            'date_from',
            sa.DateTime(timezone=True),
            nullable=True,
            comment='Start date for data collection (inclusive)',
        ),
        schema=settings.DB_SCHEMA,
    )
    op.add_column(
        'sources',
        sa.Column(
            'date_to',
            sa.DateTime(timezone=True),
            nullable=True,
            comment='End date for data collection (inclusive)',
        ),
        schema=settings.DB_SCHEMA,
    )
