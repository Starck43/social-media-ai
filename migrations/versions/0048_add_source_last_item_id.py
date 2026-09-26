"""add last_item_id to sources

Revision ID: 0048
Revises: 0047
Create Date: 2026-09-25 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0048"
down_revision: Union[str, Sequence[str], None] = "0047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "sources",
        sa.Column(
            "last_item_id",
            sa.String(length=100),
            nullable=True,
            comment="Watermark of the newest ingested item for push-based sources (Telegram Bot API)",
        ),
        schema="social_manager",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("sources", "last_item_id", schema="social_manager")
