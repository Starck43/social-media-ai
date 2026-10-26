"""Preserve transition actor IDs and outcomes after membership/account deletion.
Revision ID: 0116
Revises: 0115
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0116"
down_revision = "0115"
branch_labels = depends_on = None
SCHEMA = settings.DB_SCHEMA


def upgrade():
    op.add_column(
        "bot_actions",
        sa.Column("history", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        schema=SCHEMA,
    )


def downgrade():
    schema = op.get_bind().dialect.identifier_preparer.quote_schema(SCHEMA)
    if (
        op.get_bind()
        .execute(sa.text(f"SELECT 1 FROM {schema}.bot_actions WHERE json_array_length(history) > 0 LIMIT 1"))
        .first()
    ):
        raise RuntimeError("Cannot discard action transition evidence; export/reconcile explicitly first")
    op.drop_column("bot_actions", "history", schema=SCHEMA)
