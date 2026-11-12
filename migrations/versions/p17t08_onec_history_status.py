"""P17-T08 align the 1C query history status set with the states the service writes.

Revision ID: p17t08_onec_history_status
Revises: p17t03_onec_history
The task revision is a placeholder; rebase and assign the final number at merge.
"""

import sqlalchemy as sa
from alembic import context, op

from app.core.config import settings

revision = "p17t08_onec_history_status"
down_revision = "p17t03_onec_history"
branch_labels = None
depends_on = None

OLD_STATUSES = "'planned','executed','refused','exported'"
NEW_STATUSES = "'executed','exported'"


def upgrade():
    schema = settings.DB_SCHEMA
    if context.is_offline_mode():
        raise RuntimeError("Cannot safely narrow the 1C query history statuses offline")
    connection = op.get_bind()
    quoted = connection.dialect.identifier_preparer.quote_schema(schema)
    connection.execute(sa.text(f"LOCK TABLE {quoted}.onec_query_history IN ACCESS EXCLUSIVE MODE"))
    stale = connection.scalar(
        sa.text(f"SELECT COUNT(*) FROM {quoted}.onec_query_history WHERE status NOT IN ({NEW_STATUSES})")
    )
    if stale:
        raise RuntimeError(
            "Upgrade refused: 1C query history rows carry a status the service no longer writes. "
            "Review those rows before narrowing the constraint."
        )
    op.drop_constraint("ck_onec_query_history_status", "onec_query_history", schema=schema, type_="check")
    op.create_check_constraint(
        "ck_onec_query_history_status", "onec_query_history", f"status IN ({NEW_STATUSES})", schema=schema
    )
    op.alter_column(
        "onec_query_history",
        "status",
        existing_type=sa.String(20),
        nullable=False,
        server_default=None,
        schema=schema,
    )


def downgrade():
    schema = settings.DB_SCHEMA
    op.alter_column(
        "onec_query_history",
        "status",
        existing_type=sa.String(20),
        nullable=False,
        server_default="planned",
        schema=schema,
    )
    op.drop_constraint("ck_onec_query_history_status", "onec_query_history", schema=schema, type_="check")
    op.create_check_constraint(
        "ck_onec_query_history_status", "onec_query_history", f"status IN ({OLD_STATUSES})", schema=schema
    )
