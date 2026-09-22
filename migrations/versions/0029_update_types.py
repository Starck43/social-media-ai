"""update types

Revision ID: 0029
Revises: 0028
Create Date: 2025-10-15 13:04:04.737961

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0029'
down_revision: Union[str, Sequence[str], None] = '0028'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade():
    # 1) Create type if missing (Postgres has no IF NOT EXISTS for TYPE)
    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'llm_strategy_type' AND n.nspname = 'social_manager'
      ) THEN
        CREATE TYPE social_manager.llm_strategy_type AS ENUM (
          'cost_efficient',
          'quality',
          'multimodal'
        );
      END IF;
    END$$;
    """)

    # 2) Alter column with USING cast
    op.alter_column(
        'bot_scenarios',
        'llm_strategy',
        schema='social_manager',
        type_=sa.Enum(
            'cost_efficient',
            'quality',
            'multimodal',
            name='llm_strategy_type',
            schema='social_manager',
            inherit_schema=True,
        ),
        postgresql_using="llm_strategy::social_manager.llm_strategy_type",
        existing_nullable=True,  # set to what it currently is
    )

    # 3) Optional: set default and/or NOT NULL
    op.execute("""
        ALTER TABLE social_manager.bot_scenarios
        ALTER COLUMN llm_strategy SET DEFAULT 'cost_efficient'::social_manager.llm_strategy_type
    """)
    # If you need NOT NULL:
    # op.execute("""
    #     ALTER TABLE social_manager.bot_scenarios
    #     ALTER COLUMN llm_strategy SET NOT NULL
    # """)


def downgrade():
    # Revert to VARCHAR (before dropping type)
    op.alter_column(
        'bot_scenarios',
        'llm_strategy',
        schema='social_manager',
        type_=sa.String(length=50),
        postgresql_using="llm_strategy::text",
        existing_nullable=True,
    )
    # Drop the enum if nothing else uses it
    op.execute("DROP TYPE IF EXISTS social_manager.llm_strategy_type")

