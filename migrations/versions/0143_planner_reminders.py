"""Durable planner reminders and notification-owned delivery attempts.

Revision ID: 0143
Revises: 0145

The inbox remains the durable fallback. External delivery has one durable,
single-attempt row per notification, recipient and channel.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0143"
down_revision = "0145"
branch_labels = None
depends_on = None


def upgrade():
    schema = settings.DB_SCHEMA
    op.create_table(
        "planner_reminders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("recipient_user_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False, server_default="inbox"),
        sa.Column("preset", sa.String(20), nullable=False),
        sa.Column("remind_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snoozed_until", sa.DateTime(timezone=True)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("notification_id", sa.Integer()),
        sa.Column("last_error", sa.String(60)),
        sa.Column("late", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], [f"{schema}.tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            [f"{schema}.planner_tasks.tenant_id", f"{schema}.planner_tasks.id"],
            ondelete="CASCADE",
            name="fk_planner_reminder_task",
        ),
        sa.ForeignKeyConstraint(["recipient_user_id"], [f"{schema}.users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["notification_id"], [f"{schema}.notifications.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("tenant_id", "task_id", "recipient_user_id", "channel", name="uq_planner_reminder_owner"),
        sa.CheckConstraint("channel IN ('inbox','telegram','vk','email','max')", name="ck_planner_reminder_channel"),
        sa.CheckConstraint("preset IN ('day','week','month','custom')", name="ck_planner_reminder_preset"),
        sa.CheckConstraint("state IN ('pending','deferred','delivered','cancelled')", name="ck_planner_reminder_state"),
        sa.CheckConstraint("version > 0 AND generation > 0", name="ck_planner_reminder_generation"),
        sa.CheckConstraint("(state = 'delivered') = (sent_at IS NOT NULL)", name="ck_planner_reminder_delivery_time"),
        schema=schema,
    )
    op.create_index(
        "ix_planner_reminder_due", "planner_reminders", ["tenant_id", "state", "next_attempt_at"], schema=schema
    )

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("notification_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(60)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], [f"{schema}.tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["notification_id"], [f"{schema}.notifications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], [f"{schema}.users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["notification_id", "user_id"],
            [f"{schema}.notification_receipts.notification_id", f"{schema}.notification_receipts.user_id"],
            ondelete="CASCADE",
            name="fk_notification_delivery_receipt",
        ),
        sa.UniqueConstraint("notification_id", "user_id", "channel", name="uq_notification_delivery"),
        sa.CheckConstraint("channel IN ('telegram','vk','email','max')", name="ck_notification_delivery_channel"),
        sa.CheckConstraint(
            "state IN ('pending','sending','sent','blocked','unknown')", name="ck_notification_delivery_state"
        ),
        schema=schema,
    )
    op.create_index(
        "ix_notification_delivery_pending",
        "notification_deliveries",
        ["tenant_id", "state", "next_attempt_at"],
        schema=schema,
    )


def downgrade():
    schema = settings.DB_SCHEMA
    bind = op.get_bind()
    quoted = bind.dialect.identifier_preparer.quote_schema(schema)
    # Keep the emptiness check and DROP atomic with concurrent producers. An
    # advisory maintenance lock alone does not fence ordinary application writes.
    bind.execute(
        sa.text(f"LOCK TABLE {quoted}.notification_deliveries, {quoted}.planner_reminders IN ACCESS EXCLUSIVE MODE")
    )
    for table in ("notification_deliveries", "planner_reminders"):
        count = bind.scalar(sa.text(f"SELECT count(*) FROM {quoted}.{table}"))
        if count:
            raise RuntimeError(f"Downgrade refused: {table} contains durable reminder data")
    op.drop_table("notification_deliveries", schema=schema)
    op.drop_table("planner_reminders", schema=schema)
