"""ai_analytics.estimated_cost: integer cents -> numeric(14,6) cents

The column was INTEGER, so any cost under half a cent disappeared: a 600-token
call on a $0.15/1M model costs ~0.02c, rounded to 0, and the write path then
stored NULL (`if cost > 0 else None`). A day of small analyses therefore showed
"—" in the admin and SUM() under-reported the real spend.

The storage unit stays USD cents — the whole reporting layer (dashboard,
ReportAggregator, detail template) divides by 100 — so existing rows keep their
meaning and the cast is a lossless widening. Downgrade truncates fractions back
to whole cents.

Revision ID: 0060
Revises: 0059
Create Date: 2026-09-27

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0060"
down_revision: Union[str, Sequence[str], None] = "0059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "social_manager"

# Kept byte-identical to AIAnalytics.estimated_cost's `comment=` so `alembic check`
# reports no comment drift between the model and the database.
COLUMN_COMMENT = "Estimated cost in USD cents at 1e-8 USD precision (NULL = unknown or free)"


def upgrade() -> None:
    op.alter_column(
        "ai_analytics",
        "estimated_cost",
        existing_type=sa.Integer(),
        type_=sa.Numeric(14, 6),
        existing_nullable=True,
        schema=SCHEMA,
        postgresql_using="estimated_cost::numeric(14,6)",
    )
    op.execute(f"COMMENT ON COLUMN {SCHEMA}.ai_analytics.estimated_cost IS '{COLUMN_COMMENT}'")


def downgrade() -> None:
    op.alter_column(
        "ai_analytics",
        "estimated_cost",
        existing_type=sa.Numeric(14, 6),
        type_=sa.Integer(),
        existing_nullable=True,
        schema=SCHEMA,
        postgresql_using="round(estimated_cost)",
    )
    # Pre-0060 the column carried no DB comment — drop it so a downgrade leaves the
    # schema byte-identical to what 0059 produced.
    op.execute(f"COMMENT ON COLUMN {SCHEMA}.ai_analytics.estimated_cost IS NULL")
