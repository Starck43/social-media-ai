"""Drop unused requires_approval column from agent_tasks.

The flag was never read by the runtime: analyze jobs create BotAction rows in
PENDING status regardless, and no approval workflow consumes the column.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

from app.core.config import settings

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None
schema = settings.DB_SCHEMA


def _column_exists(conn) -> bool:
    return bool(
        conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = 'agent_tasks' "
                "AND column_name = 'requires_approval'"
            ),
            {"schema": schema},
        ).scalar()
    )


def upgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn):
        op.drop_column("agent_tasks", "requires_approval", schema=schema)


def downgrade() -> None:
    conn = op.get_bind()
    if not _column_exists(conn):
        op.add_column(
            "agent_tasks",
            sa.Column(
                "requires_approval",
                sa.Boolean(),
                nullable=False,
                server_default=text("true"),
            ),
            schema=schema,
        )
