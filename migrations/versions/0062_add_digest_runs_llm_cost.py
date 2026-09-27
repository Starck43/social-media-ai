"""add digest_runs.llm_cost — digest spend feeds the daily cap

AGENT_DAILY_COST_LIMIT is documented as a cap across "agent + digests per
day", but only agent_messages.cost was ever accounted: the digest builder
priced its LLM summary nowhere, so part of the daily spend was invisible to
the cap. This column stores the USD cost of the summary (priced from
llm_models tariffs), summed per UTC day by DigestRunManager.cost_today()
alongside agent_messages.cost_today() (see tenancy.resolver.daily_cost_today).

NULL = no summary was requested/priced (cap skipped it, no model, or unknown
tariffs); consumers treat it as 0.

Revision ID: 0062
Revises: 0061
Create Date: 2026-09-27

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0062"
down_revision: Union[str, Sequence[str], None] = "0061"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "social_manager"


def upgrade() -> None:
    op.add_column("digest_runs", sa.Column("llm_cost", sa.Float(), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column("digest_runs", "llm_cost", schema=SCHEMA)
