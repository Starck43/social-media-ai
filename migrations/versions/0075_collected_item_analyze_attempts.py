"""track how often a staged item failed analysis, and give up eventually

Revision ID: 0075
Revises: 0074
Create Date: 2026-10-05

A staged item is retired only once an analysis has actually stored it
(`handle_analyze` -> `_retire_staged`). An item whose LLM call times out stores
nothing, so it stays staged and is picked up again by the next run — forever.
With a backlog larger than `ANALYZE_STAGE_BATCH` the same unanalysable rows were
retried on every pass, each costing a full request timeout, and the run never
drained.

`analyze_attempts` counts those failures and `give_up_after_attempts` is the
ceiling: once reached, `for_source` stops handing the row out, so it stops
poisoning the batch while staying on disk for `handle_prune` (the raw copy is
still the only copy — it is never deleted for failing).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision: str = "0075"
down_revision: Union[str, None] = "0074"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "collected_items"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column("analyze_attempts", sa.Integer(), nullable=False, server_default="0"),
        schema=settings.DB_SCHEMA,
    )
    # The ceiling the handler compares against. A column rather than a constant
    # so an operator can widen or tighten it for one workspace's backlog without
    # a redeploy.
    op.add_column(
        TABLE,
        sa.Column("give_up_after_attempts", sa.Integer(), nullable=False, server_default="3"),
        schema=settings.DB_SCHEMA,
    )
    # `for_source` filters on `analyze_attempts < give_up_after_attempts` per row,
    # so a row's own ceiling travels with it.
    op.create_index(
        "ix_collected_items_source_attempts",
        TABLE,
        ["source_id", "analyze_attempts"],
        schema=settings.DB_SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_collected_items_source_attempts", table_name=TABLE, schema=settings.DB_SCHEMA)
    op.drop_column(TABLE, "give_up_after_attempts", schema=settings.DB_SCHEMA)
    op.drop_column(TABLE, "analyze_attempts", schema=settings.DB_SCHEMA)
