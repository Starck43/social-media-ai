"""User-owned notification receipts; no inferred historical recipients.
Revision ID: 0113
Revises: 0112
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0113"
down_revision = "0112"
branch_labels = depends_on = None
SCHEMA = settings.DB_SCHEMA


def upgrade():
    op.add_column(
        "notifications", sa.Column("severity", sa.String(20), nullable=False, server_default="info"), schema=SCHEMA
    )
    op.add_column("notifications", sa.Column("dedup_key", sa.String(200)), schema=SCHEMA)
    op.add_column("notifications", sa.Column("expires_at", sa.DateTime(timezone=True)), schema=SCHEMA)
    op.create_check_constraint(
        "ck_notifications_severity",
        "notifications",
        "severity IN ('info', 'warning', 'action_required')",
        schema=SCHEMA,
    )
    op.create_index(
        "uq_notifications_tenant_dedup",
        "notifications",
        ["tenant_id", "dedup_key"],
        unique=True,
        postgresql_where=sa.text("dedup_key IS NOT NULL"),
        schema=SCHEMA,
    )
    op.create_table(
        "notification_receipts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "notification_id",
            sa.Integer,
            sa.ForeignKey(f"{SCHEMA}.notifications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("dismissed_at", sa.DateTime(timezone=True)),
        sa.Column("snoozed_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("notification_id", "user_id", name="uq_notification_receipt_owner"),
        schema=SCHEMA,
    )
    op.create_index("ix_notification_receipts_user_id", "notification_receipts", ["user_id"], schema=SCHEMA)
    # Old is_read belongs to the workspace. Copying it to any user's receipt
    # would invent recipient/acknowledgement evidence. Archive rows stay hidden.


def downgrade():
    op.drop_table("notification_receipts", schema=SCHEMA)
    op.drop_index("uq_notifications_tenant_dedup", table_name="notifications", schema=SCHEMA)
    op.drop_constraint("ck_notifications_severity", "notifications", type_="check", schema=SCHEMA)
    for name in ("expires_at", "dedup_key", "severity"):
        op.drop_column("notifications", name, schema=SCHEMA)
