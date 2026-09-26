"""add task_templates

Revision ID: 0050
Revises: 0049
Create Date: 2026-09-25 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0050"
down_revision: Union[str, Sequence[str], None] = "0049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey("social_manager.tenants.id", ondelete="CASCADE"),
            nullable=True,
            comment="NULL = global default template, available to all workspaces",
        ),
        sa.Column("slug", sa.String(length=64), nullable=False, comment="Machine name: digest, alert, research, answer"),
        sa.Column("name", sa.String(length=255), nullable=False, comment="Human-readable name"),
        sa.Column("purpose", sa.Text(), nullable=True, comment="What this task does (shown in task_list)"),
        sa.Column("system_prompt", sa.Text(), nullable=True, comment="Overrides AGENT_SYSTEM_PROMPT for this task"),
        sa.Column(
            "user_prompt_template",
            sa.Text(),
            nullable=True,
            comment="Template for user message; {query} = user input",
        ),
        sa.Column("output_schema", sa.JSON(), nullable=True, comment="JSON Schema for structured LLM output"),
        sa.Column(
            "model_id",
            sa.Integer(),
            sa.ForeignKey("social_manager.llm_models.id", ondelete="SET NULL"),
            nullable=True,
            comment="Optional model override for this task",
        ),
        sa.Column("temperature", sa.Float(), nullable=True, comment="Temperature override (0.0–2.0)"),
        sa.Column("max_tokens", sa.Integer(), nullable=True, comment="Max tokens override"),
        sa.Column("tools", sa.JSON(), nullable=True, comment="Allowlist of tool names; None = all tools available"),
        sa.Column(
            "requires_confirmation",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("cron_expr", sa.String(length=100), nullable=True, comment="Optional cron schedule for auto-execution"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Default template for the tenant when no slug matches",
        ),
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
        sa.UniqueConstraint("tenant_id", "slug", name="uq_task_template_tenant_slug"),
        sa.Index("ix_task_templates_tenant_id", "tenant_id"),
        sa.Index("ix_task_templates_tenant_id_slug", "tenant_id", "slug"),
        schema="social_manager",
    )


def downgrade() -> None:
    op.drop_table("task_templates", schema="social_manager")