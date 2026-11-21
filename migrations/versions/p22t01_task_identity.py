"""Add the canonical task aggregate alongside the legacy planner."""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "p22t01_task_identity"
down_revision = "p12t12_s1_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    s = settings.DB_SCHEMA
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey(f"{s}.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assignee_id", sa.Integer(), sa.ForeignKey(f"{s}.users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title_encrypted", sa.String(), nullable=False),
        sa.Column("description_encrypted", sa.String(), nullable=True),
        sa.Column("series_template_encrypted", sa.String(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("visibility", sa.String(16), nullable=False),
        sa.CheckConstraint("visibility IN ('private','shared','workspace')", name="ck_task_visibility"),
        sa.Column("priority", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey(f"{s}.contacts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("series_id", sa.Integer(), nullable=True),
        sa.Column("previous_occurrence_id", sa.Integer(), nullable=True),
        sa.Column("occurrence_key", sa.String(64), nullable=False),
        sa.Column("occurrence_index", sa.Integer(), nullable=False),
        sa.Column("recurrence", sa.Text(), nullable=True),
        sa.Column("recurrence_source", sa.String(100), nullable=False, server_default="planner"),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("pii_updated_at", sa.BigInteger(), nullable=False, server_default="1"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_task_task_tenant"),
        sa.UniqueConstraint("tenant_id", "series_id", "occurrence_key", name="uq_task_occurrence"),
        sa.UniqueConstraint("tenant_id", "previous_occurrence_id", name="uq_task_successor"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "series_id"], [f"{s}.tasks.tenant_id", f"{s}.tasks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_id"], [f"{s}.tasks.tenant_id", f"{s}.tasks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "previous_occurrence_id"],
            [f"{s}.tasks.tenant_id", f"{s}.tasks.id"],
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        # Global SET NULL above preserves the task on contact erasure; deferred
        # composite validation fences the original link by workspace (PG14+).
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            [f"{s}.contacts.tenant_id", f"{s}.contacts.id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint("priority IN ('low','medium','high','critical')", name="ck_task_priority"),
        sa.CheckConstraint("status IN ('todo','in_progress','done','cancelled')", name="ck_task_status"),
        sa.CheckConstraint("due_at IS NULL OR due_date IS NULL", name="ck_task_deadline"),
        sa.CheckConstraint(
            "recurrence IS NULL OR due_at IS NOT NULL OR due_date IS NOT NULL", name="ck_task_recurrence_due"
        ),
        sa.CheckConstraint("version > 0 AND occurrence_index >= 0", name="ck_task_versions"),
        sa.CheckConstraint("parent_id IS NULL OR parent_id != id", name="ck_task_parent"),
        sa.CheckConstraint("(status = 'done') = (completed_at IS NOT NULL)", name="ck_task_completion"),
        schema=s,
    )
    op.create_index("ix_task_workspace_due", "tasks", ["tenant_id", "status", "due_at", "due_date"], schema=s)
    schema = op.get_bind().dialect.identifier_preparer.quote_schema(s)
    op.execute(
        f"CREATE TRIGGER contact_pii_marker BEFORE INSERT OR UPDATE ON {schema}.tasks FOR EACH ROW EXECUTE FUNCTION {schema}.bump_contact_pii_marker()"
    )
    op.create_index("ix_task_owner", "tasks", ["tenant_id", "owner_user_id", "id"], schema=s)
    op.create_index("ix_task_assignee", "tasks", ["tenant_id", "assignee_id", "id"], schema=s)
    op.create_table(
        "task_participants",
        sa.Column("tenant_id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey(f"{s}.users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(16), primary_key=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"], [f"{s}.tasks.tenant_id", f"{s}.tasks.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint("role IN ('collaborator','watcher')", name="ck_task_participant_role"),
        schema=s,
    )


def downgrade() -> None:
    op.drop_table("task_participants", schema=settings.DB_SCHEMA)
    op.drop_table("tasks", schema=settings.DB_SCHEMA)
