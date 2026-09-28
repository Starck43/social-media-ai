"""agent_task_sources m2m + flatten task payload

Creates the many-to-many `agent_task_sources` table between `agent_tasks` and
`sources`, then migrates existing `payload["source_ids"]` lists into m2m rows.

Adds `agent_tasks.agent_scenario_id` (FK to agent_scenarios) and migrates the old
`payload["scenario_id"]` into it — the task now references its scenario via the
relationship instead of a payload key.

Flattens the task payload: removes the old `source_ids` key (now an m2m
relationship) and documents the new flat keys `monitored_users` / `excluded_users`
(comma/space-separated lists of usernames). No existing data uses the latter two,
so only `source_ids` needs a real data migration.

Revision ID: 0063
Revises: 0062
Create Date: 2026-09-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0063"
down_revision: Union[str, Sequence[str], None] = "0062"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "social_manager"


def upgrade() -> None:
    op.create_table(
        "agent_task_sources",
        sa.Column("agent_task_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_task_id"],
            [f"{SCHEMA}.agent_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            [f"{SCHEMA}.sources.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("agent_task_id", "source_id"),
        schema=SCHEMA,
    )

    # Migrate existing payload["source_ids"] lists into m2m rows.
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.agent_task_sources (agent_task_id, source_id)
        SELECT
            t.id AS agent_task_id,
            sid::int AS source_id
        FROM {SCHEMA}.agent_tasks t
        CROSS JOIN LATERAL jsonb_array_elements_text(
            COALESCE(t.payload::jsonb->'source_ids', '[]'::jsonb)
        ) AS sid
        WHERE t.payload::jsonb->'source_ids' IS NOT NULL
          AND jsonb_array_length(t.payload::jsonb->'source_ids') > 0
        ON CONFLICT DO NOTHING
        """
    )

    # Add the scenario FK column and migrate payload["scenario_id"] into it.
    op.add_column(
        "agent_tasks",
        sa.Column("agent_scenario_id", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_agent_tasks_agent_scenario",
        "agent_tasks",
        "agent_scenarios",
        ["agent_scenario_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.execute(
        f"""
        UPDATE {SCHEMA}.agent_tasks
        SET agent_scenario_id = (payload::jsonb->>'scenario_id')::int
        WHERE payload::jsonb->>'scenario_id' IS NOT NULL
          AND (payload::jsonb->>'scenario_id') ~ '^[0-9]+$'
        """
    )

    # Drop the now-redundant source_ids / scenario_id keys from payload.
    op.execute(
        f"""
        UPDATE {SCHEMA}.agent_tasks
        SET payload = (payload::jsonb - 'source_ids' - 'scenario_id')::json
        WHERE payload::jsonb ? 'source_ids' OR payload::jsonb ? 'scenario_id'
        """
    )


def downgrade() -> None:
    # Restore payload["scenario_id"] from the FK column.
    op.execute(
        f"""
        UPDATE {SCHEMA}.agent_tasks
        SET payload = jsonb_set(
            COALESCE(payload::jsonb, '{{}}'::jsonb),
            '{{scenario_id}}',
            to_jsonb(agent_scenario_id)
        )::json
        WHERE agent_scenario_id IS NOT NULL
        """
    )
    op.drop_constraint(
        "fk_agent_tasks_agent_scenario",
        "agent_tasks",
        type_="foreignkey",
        schema=SCHEMA,
    )
    op.drop_column("agent_tasks", "agent_scenario_id", schema=SCHEMA)

    # Restore payload["source_ids"] from m2m rows (best-effort, no conflict).
    op.execute(
        f"""
        UPDATE {SCHEMA}.agent_tasks t
        SET payload = jsonb_set(
            COALESCE(t.payload::jsonb, '{{}}'::jsonb),
            '{{source_ids}}',
            (
                SELECT COALESCE(jsonb_agg(s.source_id ORDER BY s.source_id), '[]'::jsonb)
                FROM {SCHEMA}.agent_task_sources s
                WHERE s.agent_task_id = t.id
            )
        )::json
        """
    )

    op.drop_table("agent_task_sources", schema=SCHEMA)

