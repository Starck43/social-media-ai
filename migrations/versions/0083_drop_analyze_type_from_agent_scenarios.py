"""drop analyze_type from agent_scenarios

Replace the old analyze_type enum column with orthogonal grouping axes
(group_by + time_breakdown) that are query-time parameters, not scenario
properties.

Revision ID: 0083
Revises: 0082
Create Date: 2026-10-06
"""

from alembic import op
import sqlalchemy as sa

# import settings to resolve the schema name at runtime
from app.core.config import settings

schema = settings.DB_SCHEMA or "public"


# revision identifiers, used by Alembic.
revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the column — the enum type may still be referenced by the
    # analyzer's internal `analyze_by` parameter, so we do NOT drop the
    # PostgreSQL type here. It will be cleaned up in a future migration
    # once the analyzer is also refactored.
    op.drop_column("agent_scenarios", "analyze_type", schema=schema)


def downgrade() -> None:
    op.add_column(
        f"{schema}.agent_scenarios",
        sa.Column(
            "analyze_type",
            sa.VARCHAR(length=32),
            nullable=True,
        ),
    )
