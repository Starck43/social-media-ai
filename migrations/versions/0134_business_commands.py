"""P13-T04: business write-approval command ledger and per-target batch items.

Revision ID: 0134
Revises: 0133
No data backfill or automatic migration of legacy BotAction records.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0134"
down_revision = "0133"
branch_labels = None
depends_on = None

COMMAND_STATES = "'draft', 'previewed', 'approved', 'claimed', 'succeeded', 'failed', 'unknown', 'cancelled', 'expired'"
TARGET_STATES = "'pending', 'claimed', 'succeeded', 'failed', 'unknown', 'cancelled'"
APPROVAL_POLICIES = "'per_item', 'per_batch', 'connection_consent'"


def upgrade() -> None:
    s = settings.DB_SCHEMA
    op.create_table(
        "business_commands",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("capability", sa.String(80), nullable=False),
        sa.Column("capability_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("approval_policy", sa.String(30), nullable=False, server_default="per_item"),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey(f"{s}.users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("target_refs", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("payload_hash", sa.String(71), nullable=False),
        sa.Column("preview_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("state", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("approved_user_id", sa.Integer(), sa.ForeignKey(f"{s}.users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_payload_hash", sa.String(71), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome_summary", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("reconcile_hint", sa.Text(), nullable=True),
        sa.UniqueConstraint("tenant_id", "id", name="uq_business_commands_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "actor_user_id",
            "capability",
            "idempotency_key",
            name="uq_business_commands_idempotency",
        ),
        sa.CheckConstraint(f"state IN ({COMMAND_STATES})", name="ck_business_commands_state"),
        sa.CheckConstraint(f"approval_policy IN ({APPROVAL_POLICIES})", name="ck_business_commands_policy"),
        sa.CheckConstraint("payload_hash ~ '^sha256:[0-9a-f]{64}$'", name="ck_business_commands_payload_hash"),
        sa.CheckConstraint(
            "approved_payload_hash IS NULL OR approved_payload_hash ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_business_commands_approved_hash",
        ),
        schema=s,
    )
    op.create_index("ix_business_commands_tenant_state", "business_commands", ["tenant_id", "state"], schema=s)
    op.create_index(
        "ix_business_commands_tenant_capability", "business_commands", ["tenant_id", "capability"], schema=s
    )

    op.create_table(
        "business_command_targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("command_id", sa.Integer(), nullable=False),
        sa.Column("target_ref", sa.String(200), nullable=False),
        sa.Column("target_idempotency_key", sa.String(160), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("provider_receipt", sa.String(160), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "command_id"],
            [f"{s}.business_commands.tenant_id", f"{s}.business_commands.id"],
            ondelete="CASCADE",
            name="fk_business_command_target_command",
        ),
        sa.UniqueConstraint("tenant_id", "command_id", "target_ref", name="uq_business_command_target_ref"),
        sa.UniqueConstraint("tenant_id", "target_idempotency_key", name="uq_business_command_target_idempotency"),
        sa.CheckConstraint(f"state IN ({TARGET_STATES})", name="ck_business_command_targets_state"),
        schema=s,
    )
    op.create_index(
        "ix_business_command_targets_command", "business_command_targets", ["tenant_id", "command_id"], schema=s
    )


def downgrade() -> None:
    s = settings.DB_SCHEMA
    op.drop_table("business_command_targets", schema=s)
    op.drop_table("business_commands", schema=s)
