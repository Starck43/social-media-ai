"""add schedules and jobs tables

Revision ID: 0041
Revises: 0040
Create Date: 2026-09-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0041'
down_revision: Union[str, Sequence[str], None] = '0040'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'schedules',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False, unique=True),
        sa.Column('cron_expr', sa.String(100), nullable=False),
        sa.Column('timezone', sa.String(64), nullable=False, server_default='Europe/Moscow'),
        sa.Column('job_type', sa.String(20), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_status', sa.String(20), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema='social_manager',
    )
    op.create_index('idx_schedules_next_run_at', 'schedules', ['next_run_at'], schema='social_manager')

    op.create_table(
        'jobs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('schedule_id', sa.Integer(), nullable=True),
        sa.Column('job_type', sa.String(20), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('run_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_attempts', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['schedule_id'], ['social_manager.schedules.id'], ondelete='SET NULL'),
        schema='social_manager',
    )
    op.create_index('idx_jobs_status_run_at', 'jobs', ['status', 'run_at'], schema='social_manager')
    op.create_index('idx_jobs_schedule_id', 'jobs', ['schedule_id'], schema='social_manager')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_jobs_schedule_id', table_name='jobs', schema='social_manager')
    op.drop_index('idx_jobs_status_run_at', table_name='jobs', schema='social_manager')
    op.drop_table('jobs', schema='social_manager')
    op.drop_index('idx_schedules_next_run_at', table_name='schedules', schema='social_manager')
    op.drop_table('schedules', schema='social_manager')
