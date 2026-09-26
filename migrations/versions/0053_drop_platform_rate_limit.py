"""drop platform rate-limit columns (legacy, never written by the runtime)

The old collector-era rate tracking on `platforms` was never wired into the
scheduler/jobs runtime: no code path calls PlatformManager.update_rate_limit.
The columns stayed NULL forever and only surfaced in the admin as read-only
labels. They are removed along with the admin reference.

Revision ID: 0053
Revises: 0052
Create Date: 2026-09-25 05:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0053"
down_revision: Union[str, Sequence[str], None] = "0052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "social_manager"


def upgrade() -> None:
    op.drop_column("platforms", "rate_limit_remaining", schema=SCHEMA)
    op.drop_column("platforms", "rate_limit_reset_at", schema=SCHEMA)


def downgrade() -> None:
    op.add_column(
        "platforms",
        sa.Column("rate_limit_remaining", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "platforms",
        sa.Column("rate_limit_reset_at", sa.DateTime(), nullable=True),
        schema=SCHEMA,
    )
