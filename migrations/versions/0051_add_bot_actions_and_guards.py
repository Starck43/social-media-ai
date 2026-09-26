"""add bot_actions table and guards to bot_scenarios

Revision ID: 0051
Revises: 0050
Create Date: 2026-09-25 03:15:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0051"
down_revision: Union[str, Sequence[str], None] = "0050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create bot_action_status enum type (bot_action_type already exists)
    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'bot_action_status' AND n.nspname = 'social_manager'
      ) THEN
        CREATE TYPE social_manager.bot_action_status AS ENUM (
          'PENDING', 'APPROVED', 'REJECTED', 'EXECUTED', 'FAILED'
        );
      END IF;
    END$$;
    """)

    # Create bot_actions table
    op.create_table(
        "bot_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey("social_manager.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "scenario_id",
            sa.Integer(),
            sa.ForeignKey("social_manager.bot_scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("social_manager.sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "analytics_id",
            sa.Integer(),
            sa.ForeignKey("social_manager.ai_analytics.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "action_type",
            postgresql.ENUM(
                "COMMENT", "REPLY", "DIRECT_MESSAGE", "POST", "REACTION", "MODERATION", "NOTIFICATION",
                name="bot_action_type",
                schema="social_manager",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDING", "APPROVED", "REJECTED", "EXECUTED", "FAILED",
                name="bot_action_status",
                schema="social_manager",
                create_type=False,
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "confirmed_by",
            sa.Integer(),
            sa.ForeignKey("social_manager.tenant_users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Index("ix_bot_actions_tenant_id", "tenant_id"),
        sa.Index("ix_bot_actions_status", "status"),
        sa.Index("ix_bot_actions_scenario_id", "scenario_id"),
        sa.Index("ix_bot_actions_source_id", "source_id"),
        schema="social_manager",
    )

    # Add guards columns to bot_scenarios
    op.add_column(
        "bot_scenarios",
        sa.Column(
            "rate_limit_per_hour",
            sa.Integer(),
            nullable=True,
            comment="Max actions per hour for this scenario",
        ),
        schema="social_manager",
    )
    op.add_column(
        "bot_scenarios",
        sa.Column(
            "cooldown_seconds",
            sa.Integer(),
            nullable=True,
            comment="Min seconds between actions",
        ),
        schema="social_manager",
    )
    op.add_column(
        "bot_scenarios",
        sa.Column(
            "requires_approval",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
            comment="Require owner approval before execution",
        ),
        schema="social_manager",
    )
    op.add_column(
        "bot_scenarios",
        sa.Column(
            "blacklist",
            sa.JSON(),
            nullable=True,
            comment="Usernames/IDs to never act on",
        ),
        schema="social_manager",
    )
    op.add_column(
        "bot_scenarios",
        sa.Column(
            "whitelist",
            sa.JSON(),
            nullable=True,
            comment="Usernames/IDs to always act on (if set, only these)",
        ),
        schema="social_manager",
    )


def downgrade() -> None:
    # Remove guards columns from bot_scenarios
    op.drop_column("bot_scenarios", "whitelist", schema="social_manager")
    op.drop_column("bot_scenarios", "blacklist", schema="social_manager")
    op.drop_column("bot_scenarios", "requires_approval", schema="social_manager")
    op.drop_column("bot_scenarios", "cooldown_seconds", schema="social_manager")
    op.drop_column("bot_scenarios", "rate_limit_per_hour", schema="social_manager")

    # Drop bot_actions table
    op.drop_table("bot_actions", schema="social_manager")

    # Drop enum type
    op.execute("DROP TYPE IF EXISTS social_manager.bot_action_status")
