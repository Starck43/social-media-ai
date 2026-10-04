"""move trigger and action configuration from agent_scenarios to agent_tasks

Revision ID: 0073
Revises: 0072
Create Date: 2026-10-04

A scenario describes *how to analyse* a piece of content and is meant to be
reused; *whether to act on the analysis* belongs to the task that ran it. Two
tasks sharing a scenario may want different keywords, different actions and
different safety limits, so these columns move down to the task.

The data is copied per linked task, so every task that pointed at a scenario
inherits that scenario's configuration. A scenario with no tasks attached loses
it — there is no run that would have used it.

`bot_actions.agent_task_id` is added alongside: the guards are now read from the
task, and rate limiting/cooldown must count per task. Counting per scenario would
merge the budgets of every task sharing one scenario, which is the thing this
move exists to prevent. It is backfilled from the scenario only where the action
is unambiguous — a scenario used by exactly one task.
"""

from typing import Sequence, Union

from alembic import op

import sqlalchemy as sa

from app.core.config import settings

revision: str = "0073"
down_revision: Union[str, None] = "0072"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (scenario column, task column) — same name on both sides for all of them.
MOVED = [
    ("trigger_type", "trigger_type"),
    ("trigger_config", "trigger_config"),
    ("action_type", "action_type"),
    ("rate_limit_per_hour", "rate_limit_per_hour"),
    ("cooldown_seconds", "cooldown_seconds"),
    ("requires_approval", "requires_approval"),
    ("blacklist", "blacklist"),
    ("whitelist", "whitelist"),
]


def _add_column_if_missing(table: str, column: str, ddl: str) -> None:
    """Add a column unless the table already has it.

    Alembic stamps the version *after* running the statements, so a migration that
    died part-way (or was run twice by two sessions) leaves the schema ahead of
    the version table. Re-running it would fail on the first `ADD COLUMN` it has
    already done, so each one checks first. This keeps the migration restartable,
    which matters because the copy below is not free to redo.
    """
    schema = settings.DB_SCHEMA
    exists = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = :table AND column_name = :column"
            ),
            {"schema": schema, "table": table, "column": column},
        )
        .scalar()
    )
    if not exists:
        op.execute(ddl.format(schema=schema))


def _add_task_columns() -> None:
    schema = settings.DB_SCHEMA
    _add_column_if_missing(
        "agent_tasks",
        "trigger_type",
        f"ALTER TABLE {{schema}}.agent_tasks ADD COLUMN trigger_type {{schema}}.bot_trigger_type",
    )
    _add_column_if_missing(
        "agent_tasks", "trigger_config", "ALTER TABLE {schema}.agent_tasks ADD COLUMN trigger_config JSON"
    )
    _add_column_if_missing(
        "agent_tasks",
        "action_type",
        f"ALTER TABLE {{schema}}.agent_tasks ADD COLUMN action_type {{schema}}.bot_action_type",
    )
    _add_column_if_missing(
        "agent_tasks", "rate_limit_per_hour", "ALTER TABLE {schema}.agent_tasks ADD COLUMN rate_limit_per_hour INTEGER"
    )
    _add_column_if_missing(
        "agent_tasks", "cooldown_seconds", "ALTER TABLE {schema}.agent_tasks ADD COLUMN cooldown_seconds INTEGER"
    )
    _add_column_if_missing(
        "agent_tasks",
        "requires_approval",
        "ALTER TABLE {schema}.agent_tasks ADD COLUMN requires_approval BOOLEAN NOT NULL DEFAULT true",
    )
    _add_column_if_missing("agent_tasks", "blacklist", "ALTER TABLE {schema}.agent_tasks ADD COLUMN blacklist JSON")
    _add_column_if_missing("agent_tasks", "whitelist", "ALTER TABLE {schema}.agent_tasks ADD COLUMN whitelist JSON")


def _copy_scenario_config_to_tasks() -> None:
    """Copy each scenario's configuration onto the tasks that point at it."""
    schema = settings.DB_SCHEMA
    sets = ", ".join(f"{task_col} = s.{sc_col}" for sc_col, task_col in MOVED)
    op.execute(f"""
        UPDATE {schema}.agent_tasks t
        SET {sets}
        FROM {schema}.agent_scenarios s
        WHERE t.agent_scenario_id = s.id
        """)


def upgrade() -> None:
    schema = settings.DB_SCHEMA
    _add_task_columns()
    _copy_scenario_config_to_tasks()

    _add_column_if_missing(
        "bot_actions",
        "agent_task_id",
        "ALTER TABLE {schema}.bot_actions ADD COLUMN agent_task_id INTEGER "
        f"REFERENCES {{schema}}.agent_tasks(id) ON DELETE SET NULL",
    )
    op.create_index("ix_bot_actions_agent_task_id", "bot_actions", ["agent_task_id"], schema=schema, if_not_exists=True)

    # Backfill only where the scenario has exactly one task: with several, the
    # action could belong to any of them and guessing would merge one task's
    # budget into another's. Those rows keep a NULL task and, being pre-existing,
    # fall back to counting by scenario (see GuardsChecker).
    op.execute(f"""
        UPDATE {schema}.bot_actions ba
        SET agent_task_id = only_task.id
        FROM (
            SELECT agent_scenario_id, MIN(id) AS id
            FROM {schema}.agent_tasks
            WHERE agent_scenario_id IS NOT NULL
            GROUP BY agent_scenario_id
            HAVING COUNT(*) = 1
        ) AS only_task
        WHERE ba.agent_scenario_id = only_task.agent_scenario_id
        """)

    for sc_col, _task_col in MOVED:
        op.execute(f"ALTER TABLE {schema}.agent_scenarios DROP COLUMN IF EXISTS {sc_col}")


def downgrade() -> None:
    schema = settings.DB_SCHEMA
    _add_column_if_missing(
        "agent_scenarios",
        "trigger_type",
        f"ALTER TABLE {{schema}}.agent_scenarios ADD COLUMN trigger_type {{schema}}.bot_trigger_type",
    )
    _add_column_if_missing(
        "agent_scenarios", "trigger_config", "ALTER TABLE {schema}.agent_scenarios ADD COLUMN trigger_config JSON"
    )
    _add_column_if_missing(
        "agent_scenarios",
        "action_type",
        f"ALTER TABLE {{schema}}.agent_scenarios ADD COLUMN action_type {{schema}}.bot_action_type",
    )
    _add_column_if_missing(
        "agent_scenarios",
        "rate_limit_per_hour",
        "ALTER TABLE {schema}.agent_scenarios ADD COLUMN rate_limit_per_hour INTEGER",
    )
    _add_column_if_missing(
        "agent_scenarios",
        "cooldown_seconds",
        "ALTER TABLE {schema}.agent_scenarios ADD COLUMN cooldown_seconds INTEGER",
    )
    _add_column_if_missing(
        "agent_scenarios",
        "requires_approval",
        "ALTER TABLE {schema}.agent_scenarios ADD COLUMN requires_approval BOOLEAN NOT NULL DEFAULT true",
    )
    _add_column_if_missing(
        "agent_scenarios", "blacklist", "ALTER TABLE {schema}.agent_scenarios ADD COLUMN blacklist JSON"
    )
    _add_column_if_missing(
        "agent_scenarios", "whitelist", "ALTER TABLE {schema}.agent_scenarios ADD COLUMN whitelist JSON"
    )

    # A scenario shared by several tasks has no single configuration to restore;
    # the task's own is kept, which is the closest honest answer.
    sets = ", ".join(f"{sc_col} = t.{task_col}" for sc_col, task_col in MOVED)
    op.execute(f"""
        UPDATE {schema}.agent_scenarios s
        SET {sets}
        FROM {schema}.agent_tasks t
        WHERE t.agent_scenario_id = s.id
        """)

    op.execute(f"DROP INDEX IF EXISTS {schema}.ix_bot_actions_agent_task_id")
    op.execute(f"ALTER TABLE {schema}.bot_actions DROP COLUMN IF EXISTS agent_task_id")
    for _sc_col, task_col in MOVED:
        op.execute(f"ALTER TABLE {schema}.agent_tasks DROP COLUMN IF EXISTS {task_col}")
