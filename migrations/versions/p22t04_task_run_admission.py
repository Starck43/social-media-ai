"""Store canonical task runs and their trigger cursors.

Placeholder revision: the number is assigned at merge, `down_revision` points at
the live `dev` head (`0159`).
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "p22t04_task_run_admission"
down_revision = "0159"
branch_labels = None
depends_on = None


def _guard_empty(schema: str, *tables: str) -> None:
    """Refuse a downgrade that would drop rows the queue still references."""
    for table in tables:
        op.execute(sa.text(f"""DO $$ BEGIN
                LOCK TABLE "{schema}"."{table}" IN ACCESS EXCLUSIVE MODE;
                IF EXISTS (SELECT 1 FROM "{schema}"."{table}") THEN
                    RAISE EXCEPTION 'Cannot downgrade while {table} rows exist';
                END IF;
                END $$"""))


def upgrade() -> None:
    s = settings.DB_SCHEMA
    op.create_table(
        "task_triggers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("principal_id", sa.Integer(), sa.ForeignKey(f"{s}.users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("cron_expression", sa.String(100), nullable=False),
        sa.Column("timezone_name", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id", "revision"],
            [
                f"{s}.task_plan_revisions.tenant_id",
                f"{s}.task_plan_revisions.task_id",
                f"{s}.task_plan_revisions.revision",
            ],
            name="fk_task_trigger_plan_revision",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "task_id", name="uq_task_trigger_task"),
        sa.UniqueConstraint("tenant_id", "task_id", "id", name="uq_task_trigger_scope"),
        sa.CheckConstraint("enabled = (next_due_at IS NOT NULL)", name="ck_task_trigger_cursor"),
        schema=s,
    )
    op.create_index(
        "ix_task_trigger_due",
        "task_triggers",
        ["tenant_id", "next_due_at"],
        schema=s,
        postgresql_where=sa.text("enabled"),
    )
    op.create_table(
        "task_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("trigger_id", sa.Integer(), nullable=True),
        sa.Column("occurrence_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manual_actor_id", sa.Integer(), nullable=True),
        sa.Column("request_key", sa.String(64), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("principal_id", sa.Integer(), sa.ForeignKey(f"{s}.users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("payer_user_id", sa.Integer(), sa.ForeignKey(f"{s}.users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("admitted_inputs_encrypted", sa.Text(), nullable=False),
        sa.Column("checkpoint_encrypted", sa.Text(), nullable=True),
        sa.Column("result", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id", "revision"],
            [
                f"{s}.task_plan_revisions.tenant_id",
                f"{s}.task_plan_revisions.task_id",
                f"{s}.task_plan_revisions.revision",
            ],
            name="fk_task_run_plan_revision",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id", "trigger_id"],
            [f"{s}.task_triggers.tenant_id", f"{s}.task_triggers.task_id", f"{s}.task_triggers.id"],
            name="fk_task_run_trigger_scope",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_task_run_scope"),
        sa.UniqueConstraint("tenant_id", "trigger_id", "occurrence_at", name="uq_task_run_occurrence"),
        sa.UniqueConstraint("tenant_id", "manual_actor_id", "request_key", name="uq_task_run_manual_intent"),
        sa.UniqueConstraint("job_id", name="uq_task_run_initial_job"),
        sa.CheckConstraint(
            "(trigger_id IS NULL AND occurrence_at IS NULL AND manual_actor_id IS NOT NULL"
            " AND request_key IS NOT NULL) OR (trigger_id IS NOT NULL AND occurrence_at IS NOT NULL"
            " AND manual_actor_id IS NULL AND request_key IS NULL)",
            name="ck_task_run_origin",
        ),
        sa.CheckConstraint(
            "status IN ('pending','running','done','failed','cancelled','unknown','skipped')",
            name="ck_task_run_status",
        ),
        schema=s,
    )
    op.create_index(
        "uq_task_run_active",
        "task_runs",
        ["tenant_id", "task_id"],
        unique=True,
        schema=s,
        postgresql_where=sa.text("status IN ('pending','running')"),
    )
    op.add_column("jobs", sa.Column("task_run_id", sa.Integer(), nullable=True), schema=s)
    op.create_foreign_key(
        "fk_job_task_run_scope",
        "jobs",
        "task_runs",
        ["tenant_id", "task_run_id"],
        ["tenant_id", "id"],
        source_schema=s,
        referent_schema=s,
        ondelete="CASCADE",
    )
    op.create_index("ix_jobs_task_run_id", "jobs", ["task_run_id"], schema=s)


def downgrade() -> None:
    s = settings.DB_SCHEMA
    op.drop_index("ix_jobs_task_run_id", table_name="jobs", schema=s)
    op.drop_constraint("fk_job_task_run_scope", "jobs", type_="foreignkey", schema=s)
    op.drop_column("jobs", "task_run_id", schema=s)
    _guard_empty(s, "task_runs")
    op.drop_index("uq_task_run_active", table_name="task_runs", schema=s)
    op.drop_table("task_runs", schema=s)
    _guard_empty(s, "task_triggers")
    op.drop_index("ix_task_trigger_due", table_name="task_triggers", schema=s)
    op.drop_table("task_triggers", schema=s)
