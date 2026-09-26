"""rename bot_scenarios/schedules to agent_scenarios/agent_tasks; drop task_templates

Revision ID: 0059
Revises: 0058
Create Date: 2026-09-26

Renames:
- bot_scenarios -> agent_scenarios
- schedules -> agent_tasks
FK columns:
- sources.bot_scenario_id -> agent_scenario_id
- jobs.schedule_id -> agent_task_id
- digest_runs.schedule_id -> agent_task_id
- bot_actions.scenario_id -> agent_scenario_id
AgentScenario gains max_tokens, output_schema; loses collection_interval_hours.
task_templates table is dropped (TaskTemplate model removed).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0059"
down_revision: Union[str, Sequence[str], None] = "0058"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "social_manager"


def _fk_name(bind, table: str, column: str) -> str:
    """Return the FK constraint name for (table, column), or None."""
    row = bind.execute(
        sa.text(
            """
            SELECT con.conname
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace ns ON ns.oid = rel.relnamespace
            WHERE con.contype = 'f'
              AND ns.nspname = :schema
              AND rel.relname = :table
              AND con.conkey = ARRAY(
                  SELECT att.attnum FROM pg_attribute att
                  WHERE att.attrelid = con.conrelid AND att.attname = :column
              )
            """
        ),
        {"schema": SCHEMA, "table": table, "column": column},
    ).fetchone()
    return row[0] if row else None


def upgrade() -> None:
    bind = op.get_bind()

    # ---- 1. Rename tables --------------------------------------------------
    op.rename_table("bot_scenarios", "agent_scenarios", schema=SCHEMA)
    op.rename_table("schedules", "agent_tasks", schema=SCHEMA)

    # ---- 2. Drop FK constraints referencing the old tables / columns -------
    # sources.bot_scenario_id
    fk = _fk_name(bind, "sources", "bot_scenario_id")
    if fk:
        op.drop_constraint(fk, "sources", type_="foreignkey", schema=SCHEMA)
    # jobs.schedule_id
    fk = _fk_name(bind, "jobs", "schedule_id")
    if fk:
        op.drop_constraint(fk, "jobs", type_="foreignkey", schema=SCHEMA)
    # digest_runs.schedule_id
    fk = _fk_name(bind, "digest_runs", "schedule_id")
    if fk:
        op.drop_constraint(fk, "digest_runs", type_="foreignkey", schema=SCHEMA)
    # bot_actions.scenario_id
    fk = _fk_name(bind, "bot_actions", "scenario_id")
    if fk:
        op.drop_constraint(fk, "bot_actions", type_="foreignkey", schema=SCHEMA)

    # ---- 3. Rename FK columns ----------------------------------------------
    op.alter_column("sources", "bot_scenario_id", new_column_name="agent_scenario_id", schema=SCHEMA)
    op.alter_column("jobs", "schedule_id", new_column_name="agent_task_id", schema=SCHEMA)
    op.alter_column("digest_runs", "schedule_id", new_column_name="agent_task_id", schema=SCHEMA)
    op.alter_column("bot_actions", "scenario_id", new_column_name="agent_scenario_id", schema=SCHEMA)

    # ---- 4. Rename / recreate indexes and constraints ----------------------
    # digest_runs unique constraint mentions schedule_id
    op.drop_constraint("uq_digest_schedule_period", "digest_runs", type_="unique", schema=SCHEMA)
    op.create_unique_constraint(
        "uq_digest_agent_task_period",
        "digest_runs",
        ["agent_task_id", "period_start", "period_end"],
        schema=SCHEMA,
    )
    # schedules indexes -> agent_tasks
    op.drop_index("ix_schedules_tenant_id", table_name="agent_tasks", schema=SCHEMA)
    op.drop_index("idx_schedules_next_run_at", table_name="agent_tasks", schema=SCHEMA)
    op.create_index("ix_agent_tasks_tenant_id", "agent_tasks", ["tenant_id"], schema=SCHEMA)
    op.create_index("idx_agent_tasks_next_run_at", "agent_tasks", ["next_run_at"], schema=SCHEMA)
    # schedules unique constraint -> agent_tasks
    op.drop_constraint("uq_schedule_tenant_name", "agent_tasks", type_="unique", schema=SCHEMA)
    op.create_unique_constraint(
        "uq_agent_task_tenant_name", "agent_tasks", ["tenant_id", "name"], schema=SCHEMA
    )
    # jobs index on schedule_id
    op.drop_index("idx_jobs_schedule_id", table_name="jobs", schema=SCHEMA)
    op.create_index("idx_jobs_agent_task_id", "jobs", ["agent_task_id"], schema=SCHEMA)
    # bot_scenarios index -> agent_scenarios
    op.drop_index("ix_bot_scenarios_tenant_id", table_name="agent_scenarios", schema=SCHEMA)
    op.create_index("ix_agent_scenarios_tenant_id", "agent_scenarios", ["tenant_id"], schema=SCHEMA)
    # bot_actions index on scenario_id
    op.drop_index("ix_bot_actions_scenario_id", table_name="bot_actions", schema=SCHEMA)
    op.create_index("ix_bot_actions_agent_scenario_id", "bot_actions", ["agent_scenario_id"], schema=SCHEMA)

    # ---- 5. Recreate FK constraints ----------------------------------------
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
    op.create_foreign_key(
        "fk_jobs_agent_task",
        "jobs",
        "agent_tasks",
        ["agent_task_id"],
        ["id"],
        ondelete="SET NULL",
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_digest_runs_agent_task",
        "digest_runs",
        "agent_tasks",
        ["agent_task_id"],
        ["id"],
        ondelete="SET NULL",
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_bot_actions_agent_scenario",
        "bot_actions",
        "agent_scenarios",
        ["agent_scenario_id"],
        ["id"],
        ondelete="CASCADE",
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )

    # ---- 6. AgentScenario schema changes ------------------------------------
    op.add_column(
        "agent_scenarios",
        sa.Column(
            "max_tokens",
            sa.Integer(),
            nullable=True,
            comment="Max tokens for LLM responses in this scenario",
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "agent_scenarios",
        sa.Column(
            "output_schema",
            sa.JSON(),
            nullable=True,
            comment="JSON Schema for structured LLM output (lazily generated from analysis_types)",
        ),
        schema=SCHEMA,
    )
    op.drop_column("agent_scenarios", "collection_interval_hours", schema=SCHEMA)

    # ---- 7. Drop task_templates --------------------------------------------
    op.drop_table("task_templates", schema=SCHEMA)


def downgrade() -> None:
    # Recreate task_templates
    op.create_table(
        "task_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey("social_manager.tenants.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("user_prompt_template", sa.Text(), nullable=True),
        sa.Column("output_schema", sa.JSON(), nullable=True),
        sa.Column(
            "model_id",
            sa.Integer(),
            sa.ForeignKey("social_manager.llm_models.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column("tools", sa.JSON(), nullable=True),
        sa.Column("requires_confirmation", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("cron_expr", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_task_template_tenant_slug"),
        sa.Index("ix_task_templates_tenant_id", "tenant_id"),
        sa.Index("ix_task_templates_tenant_id_slug", "tenant_id", "slug"),
        schema="social_manager",
    )

    # AgentScenario schema changes reversed
    op.add_column(
        "agent_scenarios",
        sa.Column("collection_interval_hours", sa.Integer(), nullable=False, server_default="1"),
        schema=SCHEMA,
    )
    op.drop_column("agent_scenarios", "output_schema", schema=SCHEMA)
    op.drop_column("agent_scenarios", "max_tokens", schema=SCHEMA)

    # Drop recreated FKs
    op.drop_constraint("fk_bot_actions_agent_scenario", "bot_actions", type_="foreignkey", schema=SCHEMA)
    op.drop_constraint("fk_digest_runs_agent_task", "digest_runs", type_="foreignkey", schema=SCHEMA)
    op.drop_constraint("fk_jobs_agent_task", "jobs", type_="foreignkey", schema=SCHEMA)
    op.drop_constraint("fk_sources_agent_scenario", "sources", type_="foreignkey", schema=SCHEMA)

    # Indexes/constraints reversed
    op.drop_index("ix_bot_actions_agent_scenario_id", table_name="bot_actions", schema=SCHEMA)
    op.create_index("ix_bot_actions_scenario_id", "bot_actions", ["scenario_id"], schema=SCHEMA)
    op.drop_index("ix_agent_scenarios_tenant_id", table_name="agent_scenarios", schema=SCHEMA)
    op.create_index("ix_bot_scenarios_tenant_id", "agent_scenarios", ["tenant_id"], schema=SCHEMA)
    op.drop_index("idx_jobs_agent_task_id", table_name="jobs", schema=SCHEMA)
    op.create_index("idx_jobs_schedule_id", "jobs", ["schedule_id"], schema=SCHEMA)
    op.drop_constraint("uq_agent_task_tenant_name", "agent_tasks", type_="unique", schema=SCHEMA)
    op.create_unique_constraint(
        "uq_schedule_tenant_name", "agent_tasks", ["tenant_id", "name"], schema=SCHEMA
    )
    op.drop_index("idx_agent_tasks_next_run_at", table_name="agent_tasks", schema=SCHEMA)
    op.drop_index("ix_agent_tasks_tenant_id", table_name="agent_tasks", schema=SCHEMA)
    op.create_index("idx_schedules_next_run_at", "agent_tasks", ["next_run_at"], schema=SCHEMA)
    op.create_index("ix_schedules_tenant_id", "agent_tasks", ["tenant_id"], schema=SCHEMA)
    op.drop_constraint("uq_digest_agent_task_period", "digest_runs", type_="unique", schema=SCHEMA)
    op.create_unique_constraint(
        "uq_digest_schedule_period",
        "digest_runs",
        ["schedule_id", "period_start", "period_end"],
        schema=SCHEMA,
    )

    # Rename FK columns back
    op.alter_column("bot_actions", "agent_scenario_id", new_column_name="scenario_id", schema=SCHEMA)
    op.alter_column("digest_runs", "agent_task_id", new_column_name="schedule_id", schema=SCHEMA)
    op.alter_column("jobs", "agent_task_id", new_column_name="schedule_id", schema=SCHEMA)
    op.alter_column("sources", "agent_scenario_id", new_column_name="bot_scenario_id", schema=SCHEMA)

    # Rename tables back
    op.rename_table("agent_tasks", "schedules", schema=SCHEMA)
    op.rename_table("agent_scenarios", "bot_scenarios", schema=SCHEMA)

    # Restore FKs
    op.create_foreign_key(
        "fk_sources_bot_scenario",
        "sources",
        "bot_scenarios",
        ["bot_scenario_id"],
        ["id"],
        ondelete="SET NULL",
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
    op.create_foreign_key(
        "jobs_schedule_id_fkey",
        "jobs",
        "schedules",
        ["schedule_id"],
        ["id"],
        ondelete="SET NULL",
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
    op.create_foreign_key(
        "digest_runs_schedule_id_fkey",
        "digest_runs",
        "schedules",
        ["schedule_id"],
        ["id"],
        ondelete="SET NULL",
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
    op.create_foreign_key(
        "bot_actions_scenario_id_fkey",
        "bot_actions",
        "bot_scenarios",
        ["scenario_id"],
        ["id"],
        ondelete="CASCADE",
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
