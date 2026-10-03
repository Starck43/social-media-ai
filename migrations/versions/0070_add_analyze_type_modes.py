"""add sources/monitored_users to analyze_type enum

AgentScenario.analyze_type drives how collected content is grouped into
analysis chains (and so how retrospectives are built). It previously supported
only `themes` and `days`; this adds `sources` and `monitored_users` so a
workspace can analyze per source or per tracked user (Source.params.monitored_users).

PostgreSQL enum types cannot be edited in place — new values are appended with
`ALTER TYPE ... ADD VALUE`, which must run outside a transaction block.

Revision ID: 0070
Revises: 0069
Create Date: 2026-10-03

"""
from typing import Sequence, Union

from alembic import op

from app.core.config import settings

# revision identifiers, used by Alembic.
revision: str = "0070"
down_revision: Union[str, None] = "0069"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# PostgreSQL requires these as separate statements (no multi-value ADD VALUE
# in a single ALTER TYPE).
_NEW_VALUES = ("sources", "monitored_users")


def upgrade() -> None:
    schema = settings.DB_SCHEMA
    for value in _NEW_VALUES:
        op.execute(f"ALTER TYPE {schema}.analyze_type ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # PostgreSQL cannot remove an enum value; a downgrade would need to rebuild
    # the type. Values are additive and harmless, so downgrade is a no-op.
    pass
