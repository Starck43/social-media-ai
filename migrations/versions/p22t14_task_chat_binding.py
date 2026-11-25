"""One private agent conversation per (workspace, user, canonical task).

Revision: placeholder `p22t14_task_chat_binding`, `down_revision` is the live
`dev` head `0162` and is renumbered at merge. The revision adds only a binding
table plus one unique index on `agent_sessions(tenant_id, id)`, which the
composite foreign key needs to stay tenant-checked in the database. Existing
main-session ids, channels and message rows are untouched, so the downgrade is a
plain drop and loses no delivered conversation.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "p22t14_task_chat_binding"
down_revision = "0162"
branch_labels = None
depends_on = None

SCHEMA = settings.DB_SCHEMA


def upgrade() -> None:
    op.create_unique_constraint("uq_agent_session_tenant_id", "agent_sessions", ["tenant_id", "id"], schema=SCHEMA)
    op.create_table(
        "task_chat_bindings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tenant_id", sa.Integer, nullable=False),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("task_id", sa.Integer, nullable=False),
        sa.Column("session_id", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], [f"{SCHEMA}.tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], [f"{SCHEMA}.users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            [f"{SCHEMA}.tasks.tenant_id", f"{SCHEMA}.tasks.id"],
            ondelete="CASCADE",
            name="fk_task_chat_binding_task_scope",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            [f"{SCHEMA}.agent_sessions.tenant_id", f"{SCHEMA}.agent_sessions.id"],
            ondelete="CASCADE",
            name="fk_task_chat_binding_session_scope",
        ),
        sa.UniqueConstraint("tenant_id", "user_id", "task_id", name="uq_task_chat_binding_actor"),
        sa.UniqueConstraint("tenant_id", "session_id", name="uq_task_chat_binding_session"),
        schema=SCHEMA,
    )
    op.create_index("ix_task_chat_binding_user", "task_chat_bindings", ["tenant_id", "user_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_task_chat_binding_user", table_name="task_chat_bindings", schema=SCHEMA)
    op.drop_table("task_chat_bindings", schema=SCHEMA)
    op.drop_constraint("uq_agent_session_tenant_id", "agent_sessions", schema=SCHEMA, type_="unique")
