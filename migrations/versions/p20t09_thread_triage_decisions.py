"""P20-T09 durable per-user thread triage decisions.

Revision ID: 0151
Revises: 0150

Stores explicit user classification overrides (`basis='user_override'`,
`is_model_fact=False`) keyed by `(tenant_id, decided_by_user_id, thread_id)`
so two workspace members never silently overwrite each other's decision.
"""

import sqlalchemy as sa
from alembic import context, op

from app.core.config import settings

revision = "0151"
down_revision = "0150"
branch_labels = None
depends_on = None


def upgrade():
    s = settings.DB_SCHEMA
    op.create_table(
        "thread_triage_decisions",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column(
            "decided_by_user_id",
            sa.Integer(),
            sa.ForeignKey(f"{s}.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", sa.String(24), nullable=False),
        sa.Column("reason_encrypted", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("pii_updated_at", sa.BigInteger(), nullable=False, server_default="1"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_thread_triage_decisions_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "decided_by_user_id",
            "thread_id",
            name="uq_thread_triage_decisions_user_thread",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "thread_id"],
            [f"{s}.message_threads.tenant_id", f"{s}.message_threads.id"],
            ondelete="CASCADE",
            name="fk_thread_triage_decisions_thread",
        ),
        sa.CheckConstraint(
            "category IN ('needs_reply', 'fyi', 'can_wait', 'uncertain')",
            name="ck_thread_triage_decisions_category",
        ),
        schema=s,
    )
    op.create_index(
        "ix_thread_triage_decisions_tenant_user_thread",
        "thread_triage_decisions",
        ["tenant_id", "decided_by_user_id", "thread_id"],
        schema=s,
    )
    quoted_schema = op.get_bind().dialect.identifier_preparer.quote_schema(s)
    op.execute(
        f"CREATE TRIGGER contact_pii_marker BEFORE INSERT OR UPDATE ON {quoted_schema}.thread_triage_decisions "
        f"FOR EACH ROW EXECUTE FUNCTION {quoted_schema}.bump_contact_pii_marker()"
    )


def downgrade():
    if context.is_offline_mode():
        raise RuntimeError("Cannot safely downgrade thread triage decisions offline")
    s = settings.DB_SCHEMA
    connection = op.get_bind()
    quoted_schema = connection.dialect.identifier_preparer.quote_schema(s)
    connection.execute(sa.text(f"LOCK TABLE {quoted_schema}.thread_triage_decisions IN ACCESS EXCLUSIVE MODE"))
    has_rows = connection.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {quoted_schema}.thread_triage_decisions)"))
    if has_rows:
        raise RuntimeError("Downgrade refused: thread_triage_decisions contains persisted user decisions")
    op.drop_index("ix_thread_triage_decisions_tenant_user_thread", table_name="thread_triage_decisions", schema=s)
    op.drop_table("thread_triage_decisions", schema=s)
