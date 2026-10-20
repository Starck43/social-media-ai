"""Add the VK search source type and drop the retired action guard lists.

`Source.source_type` gains `SEARCH`: a source that represents a global platform
search query (VK `newsfeed.search`) rather than one wall. The phrase and its
optional owner/date filters live in `sources.params`.

`agent_tasks.blacklist` / `agent_tasks.whitelist` gated *outbound actions* by
target user. They were never reachable from a realistic workspace flow and the
product decision is to remove them instead of keeping two configuration fields
nobody can explain. Rate limits and cooldowns stay: they bound how much an
automation may do, which is a different question from who it may address.

Revision ID: 0106
Revises: 0105
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0106"
down_revision = "0105"
branch_labels = None
depends_on = None
SCHEMA = settings.DB_SCHEMA


def upgrade() -> None:
    # Postgres cannot remove an enum value in place; the additive value is
    # harmless on downgrade, so only the columns are restored there.
    op.execute(f"ALTER TYPE {SCHEMA}.source_type ADD VALUE IF NOT EXISTS 'SEARCH'")
    op.drop_column("agent_tasks", "blacklist", schema=SCHEMA)
    op.drop_column("agent_tasks", "whitelist", schema=SCHEMA)
    op.add_column(
        "agent_tasks",
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("agent_tasks", "dry_run", schema=SCHEMA)
    op.add_column("agent_tasks", sa.Column("blacklist", sa.JSON(), nullable=True), schema=SCHEMA)
    op.add_column("agent_tasks", sa.Column("whitelist", sa.JSON(), nullable=True), schema=SCHEMA)
