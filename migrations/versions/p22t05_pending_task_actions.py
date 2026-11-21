"""Retain deleted tasks and persist local review proposals and ledger references."""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "p22t05_pending"
down_revision = "0160"
branch_labels = None
depends_on = None


def upgrade() -> None:
    s = settings.DB_SCHEMA
    task_key = [f"{s}.tasks.tenant_id", f"{s}.tasks.id"]
    op.add_column("tasks", sa.Column("deleted_at", sa.DateTime(timezone=True)), schema=s)
    op.create_table(
        "task_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("task_version", sa.Integer(), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), sa.ForeignKey(f"{s}.users.id", ondelete="SET NULL")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(16)),
        sa.Column("outcome_reason", sa.Text()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["tenant_id", "task_id"], task_key, ondelete="CASCADE"),
        sa.CheckConstraint(
            "(outcome IS NULL AND decided_at IS NULL) OR "
            "(outcome IS NOT NULL AND outcome IN ('accepted','rejected') AND decided_at IS NOT NULL)",
            name="ck_task_decision_outcome",
        ),
        schema=s,
    )
    op.create_index("ix_task_decision_reviewer", "task_decisions", ["tenant_id", "task_id", "reviewer_id"], schema=s)
    op.create_table(
        "task_decision_links",
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("task_id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(16), primary_key=True),
        sa.Column("source_id", sa.Integer(), primary_key=True),
        sa.ForeignKeyConstraint(["tenant_id", "task_id"], task_key, ondelete="CASCADE"),
        sa.CheckConstraint("source IN ('command','document')", name="ck_task_decision_link_source"),
        schema=s,
    )


def downgrade() -> None:
    s = settings.DB_SCHEMA
    bind = op.get_bind()
    quoted = bind.dialect.identifier_preparer.quote_schema(s)
    if bind.scalar(
        sa.text(
            f"SELECT EXISTS (SELECT 1 FROM {quoted}.tasks WHERE deleted_at IS NOT NULL)"
            f" OR EXISTS (SELECT 1 FROM {quoted}.task_decisions)"
            f" OR EXISTS (SELECT 1 FROM {quoted}.task_decision_links)"
        )
    ):
        raise RuntimeError("Cannot downgrade with durable task decisions, links or deletion evidence")
    op.drop_table("task_decision_links", schema=s)
    op.drop_table("task_decisions", schema=s)
    op.drop_column("tasks", "deleted_at", schema=s)
