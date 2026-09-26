"""add is_default to bot_scenarios

Revision ID: 0057
Revises: 0056
Create Date: 2026-09-26 16:16:19.566579

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0057"
down_revision: Union[str, Sequence[str], None] = "0056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bot_scenarios",
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Default scenario for the tenant when a source has none assigned",
        ),
        schema="social_manager",
    )


def downgrade() -> None:
    op.drop_column("bot_scenarios", "is_default", schema="social_manager")
