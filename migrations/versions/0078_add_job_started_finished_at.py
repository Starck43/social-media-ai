"""add_job_started_finished_at

Revision ID: 0078
Revises: 0077
Create Date: 2026-10-05 20:07:05.855190

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0078'
down_revision: Union[str, Sequence[str], None] = '0077'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('jobs', sa.Column('started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('jobs', sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('jobs', 'finished_at')
    op.drop_column('jobs', 'started_at')
