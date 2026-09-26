"""Learning loop: memory provenance, agent_feedback, tenant style, template revision

Revision ID: 0052
Revises: 0051
Create Date: 2026-09-25 04:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0052"
down_revision: Union[str, Sequence[str], None] = "0051"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "social_manager"


def upgrade() -> None:
    # Audit counter for owner-confirmed prompt edits
    op.add_column(
        "task_templates",
        sa.Column(
            "revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
            comment="Bumped on every owner-confirmed prompt edit",
        ),
        schema=SCHEMA,
    )

    # Provenance for learned facts
    op.add_column(
        "agent_memory",
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'manual'"),
            comment="manual (memory_set) | learn (extracted from chat) | reflect (post-dedup)",
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "agent_memory",
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        schema=SCHEMA,
    )
    op.add_column(
        "agent_memory",
        sa.Column(
            "evidence_message_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.agent_messages.id", ondelete="SET NULL"),
            nullable=True,
            comment="Chat message this fact was learned from (FK is SET NULL: messages can be cleared)",
        ),
        schema=SCHEMA,
    )

    # Owner reply-style contract, rendered into the system prompt
    op.add_column(
        "tenants",
        sa.Column(
            "agent_style",
            sa.JSON(),
            nullable=True,
            comment="Owner's reply style contract: {tone, length, language, quiet_hours}; rendered into the system prompt",
        ),
        schema=SCHEMA,
    )

    # Chat feedback ledger
    op.create_table(
        "agent_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.agent_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.agent_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("vote", sa.String(length=10), nullable=False, comment="good | bad"),
        sa.Column("note", sa.Text(), nullable=True, comment="Free-text complaint attached to /bad"),
        sa.Column("voter_channel", sa.String(length=20), nullable=True),
        sa.Column("voter_external_id", sa.String(length=100), nullable=True, comment="Channel user id of the voter"),
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
        sa.Index("ix_agent_feedback_tenant_id", "tenant_id"),
        sa.Index("ix_agent_feedback_session_id", "session_id"),
        sa.Index("ix_agent_feedback_vote", "vote"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("agent_feedback", schema=SCHEMA)
    op.drop_column("tenants", "agent_style", schema=SCHEMA)
    op.drop_column("agent_memory", "evidence_message_id", schema=SCHEMA)
    op.drop_column("agent_memory", "confidence", schema=SCHEMA)
    op.drop_column("agent_memory", "source", schema=SCHEMA)
    op.drop_column("task_templates", "revision", schema=SCHEMA)
