"""Persist legacy task aliases, explicit task links and resumable backfill evidence.

This is an additive bridge. It does not rewrite planner or agent rows during the
schema upgrade. The application-owned backfill performs the reviewed copy and
can be replayed after a concurrent edit.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "p22t06_legacy_identity"
down_revision = "0161"
branch_labels = None
depends_on = None


def upgrade() -> None:
    s = settings.DB_SCHEMA
    task_key = [f"{s}.tasks.tenant_id", f"{s}.tasks.id"]
    run_key = [f"{s}.task_backfill_runs.tenant_id", f"{s}.task_backfill_runs.id"]

    op.create_table(
        "legacy_task_ids",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("legacy_namespace", sa.String(64), nullable=False),
        sa.Column("legacy_id", sa.BigInteger(), nullable=False),
        sa.Column("task_id", sa.Integer()),
        sa.Column("source_fingerprint", sa.String(64)),
        sa.Column("access_fingerprint", sa.String(64)),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("resolution", sa.String(24), nullable=False, server_default="mapped"),
        sa.Column("resolution_reason", sa.String(120)),
        sa.Column("details", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "legacy_namespace", "legacy_id", name="uq_legacy_task_identity"),
        sa.ForeignKeyConstraint(["tenant_id", "task_id"], task_key, ondelete="CASCADE"),
        sa.CheckConstraint("legacy_id > 0", name="ck_legacy_task_id_positive"),
        sa.CheckConstraint("length(legacy_namespace) BETWEEN 1 AND 64", name="ck_legacy_task_namespace"),
        sa.CheckConstraint(
            "resolution IN ('mapped','unresolved','restricted') AND "
            "((resolution = 'mapped' AND task_id IS NOT NULL) OR "
            "(resolution IN ('unresolved','restricted') AND task_id IS NULL))",
            name="ck_legacy_task_resolution",
        ),
        schema=s,
    )
    op.create_index("ix_legacy_task_canonical", "legacy_task_ids", ["tenant_id", "task_id"], schema=s)

    op.create_table(
        "task_links",
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("from_task_id", sa.Integer(), primary_key=True),
        sa.Column("relation", sa.String(24), primary_key=True),
        sa.Column("to_task_id", sa.Integer(), primary_key=True),
        sa.Column("legacy_namespace", sa.String(64)),
        sa.Column("legacy_id", sa.BigInteger()),
        sa.Column("details", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "from_task_id"], task_key, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "to_task_id"], task_key, ondelete="CASCADE"),
        sa.CheckConstraint("from_task_id <> to_task_id", name="ck_task_link_distinct_tasks"),
        sa.CheckConstraint("relation IN ('execution_for','related')", name="ck_task_link_relation"),
        schema=s,
    )
    op.create_index("ix_task_link_target", "task_links", ["tenant_id", "to_task_id", "relation"], schema=s)

    op.create_table(
        "task_source_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("source_object_id", sa.Integer(), nullable=False),
        sa.Column("relation", sa.String(48), nullable=False),
        sa.Column("details", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "task_id"], task_key, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_object_id"],
            [f"{s}.source_objects.tenant_id", f"{s}.source_objects.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "task_id", "source_object_id", "relation", name="uq_task_source_link_identity"
        ),
        sa.CheckConstraint("length(relation) BETWEEN 1 AND 48", name="ck_task_source_link_relation"),
        schema=s,
    )
    op.create_index(
        "ix_task_source_link_source", "task_source_links", ["tenant_id", "source_object_id", "relation"], schema=s
    )

    op.create_table(
        "task_backfill_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("watermark", sa.DateTime(timezone=True), nullable=False),
        sa.Column("planner_cursor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("agent_cursor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("replay_planner_cursor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("replay_agent_cursor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("replay_until", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("counts", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("error", sa.Text()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "id", name="uq_task_backfill_run_tenant"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('running','paused','completed','failed')", name="ck_task_backfill_status"),
        schema=s,
    )
    op.create_index("ix_task_backfill_active", "task_backfill_runs", ["tenant_id", "status"], schema=s)

    op.create_table(
        "task_backfill_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("legacy_namespace", sa.String(64), nullable=False),
        sa.Column("legacy_id", sa.BigInteger(), nullable=False),
        sa.Column("task_id", sa.Integer()),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("source_fingerprint", sa.String(64)),
        sa.Column("access_fingerprint", sa.String(64)),
        sa.Column("reason", sa.String(160)),
        sa.Column("details", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "run_id"], run_key, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "task_id"], task_key),
        sa.UniqueConstraint("tenant_id", "run_id", "legacy_namespace", "legacy_id", name="uq_task_backfill_record"),
        sa.CheckConstraint(
            "outcome IN ('mapped','replayed','restricted','unresolved','skipped')", name="ck_task_backfill_outcome"
        ),
        schema=s,
    )
    op.create_index("ix_task_backfill_record_resolution", "task_backfill_records", ["tenant_id", "outcome"], schema=s)


def downgrade() -> None:
    s = settings.DB_SCHEMA
    bind = op.get_bind()
    quoted = bind.dialect.identifier_preparer.quote_schema(s)
    if bind.scalar(
        sa.text(
            f"SELECT EXISTS (SELECT 1 FROM {quoted}.legacy_task_ids) "
            f"OR EXISTS (SELECT 1 FROM {quoted}.task_links) "
            f"OR EXISTS (SELECT 1 FROM {quoted}.task_source_links) "
            f"OR EXISTS (SELECT 1 FROM {quoted}.task_backfill_records) "
            f"OR EXISTS (SELECT 1 FROM {quoted}.task_backfill_runs)"
        )
    ):
        raise RuntimeError("Cannot downgrade while legacy mappings, links or reconciliation evidence exist")
    op.drop_index("ix_task_backfill_record_resolution", table_name="task_backfill_records", schema=s)
    op.drop_table("task_backfill_records", schema=s)
    op.drop_index("ix_task_backfill_active", table_name="task_backfill_runs", schema=s)
    op.drop_table("task_backfill_runs", schema=s)
    op.drop_index("ix_task_link_target", table_name="task_links", schema=s)
    op.drop_table("task_links", schema=s)
    op.drop_index("ix_task_source_link_source", table_name="task_source_links", schema=s)
    op.drop_table("task_source_links", schema=s)
    op.drop_index("ix_legacy_task_canonical", table_name="legacy_task_ids", schema=s)
    op.drop_table("legacy_task_ids", schema=s)
