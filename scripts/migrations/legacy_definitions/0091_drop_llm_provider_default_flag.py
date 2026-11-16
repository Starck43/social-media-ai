"""Drop the provider-level default flag; only models may be the default.

`llm_providers.is_default` outranked `llm_models.is_default` in every default
resolution, so a provider flag silently overrode the operator's own default
model — the chat routed to cheap aggregator rows while the flagged model was
never tried. Two providers were even flagged at once, which forked the fleet
into an id-lottery. Auto-select is now a single, obvious rule: the model with
`llm_models.is_default` wins, otherwise the lowest active id.

The column is dropped rather than ignored, so no code path can resurrect the
ranking. `llm_models.is_default` and its per-capability uniqueness (manager)
are unchanged.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

from app.core.config import settings

revision = "0091"
down_revision = "0090"
branch_labels = None
depends_on = None
schema = settings.DB_SCHEMA
TABLE = "llm_providers"
COLUMN = "is_default"


def _column_exists(conn) -> bool:
    return bool(
        conn.execute(
            text("SELECT 1 FROM information_schema.columns WHERE table_schema = :schema AND table_name = :table "
                 "AND column_name = :column"),
            {"schema": schema, "table": TABLE, "column": COLUMN},
        ).scalar()
    )


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    if _column_exists(conn):
        op.drop_column(TABLE, COLUMN, schema=schema)


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    if not _column_exists(conn):
        op.add_column(
            TABLE,
            sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.text("false")),
            schema=schema,
        )
