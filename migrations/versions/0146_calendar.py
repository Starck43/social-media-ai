"""Calendar connection-scoped decisions (P16-T03).

Revision ID: 0146
Revises: 0143
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0146"
down_revision = "0143"
branch_labels = depends_on = None


def timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def upgrade():
    s = settings.DB_SCHEMA
    op.create_table(
        "calendar_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(1500), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey(f"{s}.planner_tasks.id", ondelete="SET NULL")),
        sa.Column("origin", sa.String(10), nullable=False),
        sa.Column("state", sa.String(10), nullable=False, server_default="synced"),
        sa.Column("etag", sa.String(1000)),
        sa.Column("task_version", sa.Integer()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("proposal_encrypted", sa.Text()),
        sa.Column("conflict_encrypted", sa.Text()),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_calendar_event_tenant"),
        sa.UniqueConstraint("tenant_id", "task_id", name="uq_calendar_event_task"),
        sa.UniqueConstraint("tenant_id", "connection_id", "external_id", name="uq_calendar_event_identity"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            [f"{s}.workspace_connections.tenant_id", f"{s}.workspace_connections.id"],
            ondelete="CASCADE",
            name="fk_calendar_event_connection",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            [f"{s}.planner_tasks.tenant_id", f"{s}.planner_tasks.id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "state IN ('synced','pending','conflict','deleted','unknown')", name="ck_calendar_event_state"
        ),
        sa.CheckConstraint("origin IN ('local','external')", name="ck_calendar_event_origin"),
        schema=s,
    )
    op.create_table(
        "calendar_choices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey(f"{s}.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", sa.Integer(), sa.ForeignKey(f"{s}.workspace_connections.id", ondelete="SET NULL")),
        *timestamps(),
        sa.UniqueConstraint("tenant_id", "owner_user_id", name="uq_calendar_choice_owner"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            [f"{s}.workspace_connections.tenant_id", f"{s}.workspace_connections.id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        schema=s,
    )


def downgrade():
    op.drop_table("calendar_choices", schema=settings.DB_SCHEMA)
    op.drop_table("calendar_events", schema=settings.DB_SCHEMA)
