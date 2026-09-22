"""add_trigger_system_and_update_enums

Revision ID: 0030
Revises: 0029
Create Date: 2025-10-15 17:22:21.701408

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0030'
down_revision: Union[str, Sequence[str], None] = '0029'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1) Create bot_trigger_type enum
    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'bot_trigger_type' AND n.nspname = 'social_manager'
      ) THEN
        CREATE TYPE social_manager.bot_trigger_type AS ENUM (
          'KEYWORD_MATCH',
          'SENTIMENT_THRESHOLD',
          'ACTIVITY_SPIKE',
          'USER_MENTION',
          'TIME_BASED',
          'MANUAL'
        );
      END IF;
    END$$;
    """)
    
    # 2) Add new columns
    op.add_column('bot_scenarios', sa.Column('trigger_type', sa.Enum('KEYWORD_MATCH', 'SENTIMENT_THRESHOLD', 'ACTIVITY_SPIKE', 'USER_MENTION', 'TIME_BASED', 'MANUAL', name='bot_trigger_type', schema='social_manager', inherit_schema=True), nullable=True), schema='social_manager')
    op.add_column('bot_scenarios', sa.Column('trigger_config', sa.JSON(), nullable=True), schema='social_manager')
    op.alter_column('bot_scenarios', 'collection_interval_hours',
               existing_type=sa.INTEGER(),
               server_default='1',
               nullable=False,
               schema='social_manager')
    op.alter_column('bot_scenarios', 'llm_strategy',
               existing_type=postgresql.ENUM('cost_efficient', 'quality', 'multimodal', name='llm_strategy_type', schema='social_manager'),
               server_default=None,
               existing_nullable=True,
               schema='social_manager')
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('bot_scenarios', 'llm_strategy',
               existing_type=postgresql.ENUM('cost_efficient', 'quality', 'multimodal', name='llm_strategy_type', schema='social_manager'),
               server_default=sa.text("'cost_efficient'::social_manager.llm_strategy_type"),
               existing_nullable=True,
               schema='social_manager')
    op.alter_column('bot_scenarios', 'collection_interval_hours',
               existing_type=sa.INTEGER(),
               server_default=None,
               nullable=True,
               schema='social_manager')
    op.drop_column('bot_scenarios', 'trigger_config', schema='social_manager')
    op.drop_column('bot_scenarios', 'trigger_type', schema='social_manager')
    
    # Drop the enum type if nothing else uses it
    op.execute("DROP TYPE IF EXISTS social_manager.bot_trigger_type")
