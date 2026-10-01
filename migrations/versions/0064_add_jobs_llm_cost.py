"""add jobs.llm_cost — learning/reflect spend feeds the daily cap

run_learn (hourly) and run_reflect (weekly) call the LLM, but
tenancy.resolver.daily_cost_today() only summed agent_messages.cost (chat)
and digest_runs.llm_cost (digest summaries), so part of the daily spend was
invisible to AGENT_DAILY_COST_LIMIT / tenant.daily_cost_limit.

This column stores the priced USD cost of the learning/reflect LLM call
(priced from llm_models tariffs via the usage["cost"] block returned by
chat_with_fallback), summed per UTC day by JobManager.cost_today()
alongside the other two sources.

NULL = no LLM call was made/priced (watermark skip, cap skip, failure);
consumers treat it as 0.

Revision ID: 0064
Revises: 0063
Create Date: 2026-09-28

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0064"
down_revision: Union[str, Sequence[str], None] = "0063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "social_manager"


def upgrade() -> None:
    op.add_column("jobs", sa.Column("llm_cost", sa.Float(), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column("jobs", "llm_cost", schema=SCHEMA)
