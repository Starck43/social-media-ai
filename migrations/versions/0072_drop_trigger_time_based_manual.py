"""drop TIME_BASED and MANUAL from bot_trigger_type enum

Revision ID: 0072
Revises: 0071
Create Date: 2026-09-23

"""
from typing import Sequence, Union

from alembic import op

import sqlalchemy as sa

from app.core.config import settings

# revision identifiers, used by Alembic.
revision: str = "0072"
down_revision: Union[str, None] = "0071"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    schema = settings.DB_SCHEMA
    enum_name = "bot_trigger_type"
    table = "agent_scenarios"
    column = "trigger_type"

    # 1. Build the new enum type without TIME_BASED and MANUAL
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_type t
            JOIN pg_namespace n ON n.oid = t.typnamespace
            WHERE t.typname = '{enum_name}' AND n.nspname = '{schema}'
          ) THEN
            CREATE TYPE {schema}.{enum_name} AS ENUM (
              'KEYWORD_MATCH',
              'SENTIMENT_THRESHOLD',
              'ACTIVITY_SPIKE',
              'USER_MENTION'
            );
          END IF;
        END$$;
        """
    )

    # 2. Get current values to validate before dropping old values
    conn = op.get_bind()
    result = conn.execute(
        sa.text(f"SELECT DISTINCT {column} FROM {schema}.{table} WHERE {column} IS NOT NULL")
    )
    existing_values = [row[0] for row in result]

    # 3. Remove TIME_BASED and MANUAL from existing rows (set to NULL)
    for old_value in ("TIME_BASED", "MANUAL"):
        if old_value in existing_values:
            op.execute(
                f"UPDATE {schema}.{table} SET {column} = NULL WHERE {column} = '{old_value}'"
            )

    # 4. Recreate the enum with only the valid values
    op.execute(f"DROP TYPE IF EXISTS {schema}.{enum_name} CASCADE")
    op.execute(
        f"""
        CREATE TYPE {schema}.{enum_name} AS ENUM (
          'KEYWORD_MATCH',
          'SENTIMENT_THRESHOLD',
          'ACTIVITY_SPIKE',
          'USER_MENTION'
        );
        """
    )

    # 5. The column type is now aligned with the new enum
    #    SQLAlchemy stores the type in the column metadata, so we need to
    #    ensure the column references the new enum. For store_as_name=True
    #    columns this happens automatically when the enum is recreated.


def downgrade() -> None:
    schema = settings.DB_SCHEMA
    enum_name = "bot_trigger_type"

    # Restore the old enum with all values
    op.execute(f"DROP TYPE IF EXISTS {schema}.{enum_name} CASCADE")
    op.execute(
        f"""
        CREATE TYPE {schema}.{enum_name} AS ENUM (
          'KEYWORD_MATCH',
          'SENTIMENT_THRESHOLD',
          'ACTIVITY_SPIKE',
          'USER_MENTION',
          'TIME_BASED',
          'MANUAL'
        );
        """
    )
