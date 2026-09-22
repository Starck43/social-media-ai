"""add digest_runs table

Revision ID: 0042
Revises: 0041
Create Date: 2026-09-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0042'
down_revision: Union[str, Sequence[str], None] = '0041'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'digest_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('schedule_id', sa.Integer(), nullable=True),
        sa.Column('period', sa.String(10), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('channel', sa.String(20), nullable=False),
        sa.Column('chat_id', sa.String(100), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('message_id', sa.String(100), nullable=True),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['schedule_id'], ['social_manager.schedules.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('schedule_id', 'period_start', 'period_end', name='uq_digest_schedule_period'),
        schema='social_manager',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('digest_runs', schema='social_manager')
