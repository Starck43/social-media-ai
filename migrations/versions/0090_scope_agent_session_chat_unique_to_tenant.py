"""Scope the agent session chat uniqueness to the workspace.

`agent_sessions` is tenant-scoped, but the unique constraint covered only
(channel, chat_id). A messenger or web identity that converses with the agent
in two workspaces therefore collided: the second workspace could not see the
first one's row through the tenant guard, and the INSERT failed with a unique
violation that first-contact recovery must not adopt. The constraint now
covers (tenant_id, channel, chat_id), so each workspace keeps its own private
conversation with the same chat.

Existing rows stay as they are: previously the constraint allowed only one row
per chat, so no duplicates can appear when it is widened.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

from app.core.config import settings

revision = "0090"
down_revision = "0089"
branch_labels = None
depends_on = None
schema = settings.DB_SCHEMA

OLD_CONSTRAINT = "uq_agent_session_chat"
NEW_CONSTRAINT = "uq_agent_session_tenant_channel_chat"


def _constraint_exists(conn, name: str) -> bool:
    return bool(
        conn.execute(
            text(
                "SELECT 1 FROM pg_constraint "
                "WHERE conname = :name AND conrelid = to_regclass(:qualified)"
            ),
            {"name": name, "qualified": f"{schema}.agent_sessions"},
        ).scalar()
    )


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    if _constraint_exists(conn, OLD_CONSTRAINT):
        op.drop_constraint(OLD_CONSTRAINT, "agent_sessions", schema=schema, type_="unique")
    if not _constraint_exists(conn, NEW_CONSTRAINT):
        op.create_unique_constraint(
            NEW_CONSTRAINT, "agent_sessions", ["tenant_id", "channel", "chat_id"], schema=schema
        )


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    if _constraint_exists(conn, NEW_CONSTRAINT):
        op.drop_constraint(NEW_CONSTRAINT, "agent_sessions", schema=schema, type_="unique")
    if not _constraint_exists(conn, OLD_CONSTRAINT):
        op.create_unique_constraint(OLD_CONSTRAINT, "agent_sessions", ["channel", "chat_id"], schema=schema)
