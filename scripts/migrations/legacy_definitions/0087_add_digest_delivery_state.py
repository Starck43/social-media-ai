"""Add nullable digest delivery checkpoint storage; no invented legacy receipts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from app.core.config import settings

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None
schema = settings.DB_SCHEMA


def upgrade() -> None:
    op.add_column("digest_runs", sa.Column("delivery_state", JSONB(), nullable=True), schema=schema)


def downgrade() -> None:
    # Operators must pause senders and retain receipts before dropping this data.
    op.drop_column("digest_runs", "delivery_state", schema=schema)
