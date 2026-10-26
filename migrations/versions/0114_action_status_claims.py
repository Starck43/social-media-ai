"""Add claim/uncertain/cancelled ledger states before using them.
Revision ID: 0114
Revises: 0113
"""

from alembic import op

from app.core.config import settings

revision = "0114"
down_revision = "0113"
branch_labels = depends_on = None


def upgrade():
    schema = op.get_bind().dialect.identifier_preparer.quote_schema(settings.DB_SCHEMA)
    # PostgreSQL cannot use new enum labels until their adding transaction commits.
    with op.get_context().autocommit_block():
        for label in ("CLAIMED", "UNKNOWN", "CANCELLED"):
            op.execute(f"ALTER TYPE {schema}.bot_action_status ADD VALUE IF NOT EXISTS '{label}'")


def downgrade():
    # PostgreSQL has no DROP VALUE. Additive labels remain; 0115 refuses a
    # downgrade while any row uses a new state. Never rewrite audit outcomes.
    pass
