"""drop sources.agent_scenario_id (scenario belongs to the task, not the source)

The "Сценарий бота" field on Source was redundant: a source is reused across
tasks, and each task carries its own `agent_scenario_id`. The scenario a run
uses is now resolved from the task (with the workspace default as the fallback
for taskless paths: push ingest, CLI, agent collect, API). The per-source
column held a stale single scenario that could not express "different tasks,
different scenarios".

Revision ID: 0067
Revises: 0066
Create Date: 2026-10-03

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0067"
down_revision: Union[str, Sequence[str], None] = "0066"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "public"


def upgrade() -> None:
    op.drop_constraint("fk_sources_agent_scenario", "sources", type_="foreignkey", schema=SCHEMA)
    op.drop_column("sources", "agent_scenario_id", schema=SCHEMA)


def downgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("agent_scenario_id", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_sources_agent_scenario",
        "sources",
        "agent_scenarios",
        ["agent_scenario_id"],
        ["id"],
        ondelete="SET NULL",
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
