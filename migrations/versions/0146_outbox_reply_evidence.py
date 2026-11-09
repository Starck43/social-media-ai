"""P15-T08 durable, encrypted reply provenance.

Revision ID: 0146
Revises: 0143

A missing evidence row never changes a reply into an ordinary outbox item. The
application must enforce that property and recheck authority and approval.
"""

import sqlalchemy as sa
from alembic import context, op

from app.core.config import settings

revision = "0146"
down_revision = "0143"
branch_labels = None
depends_on = None


def upgrade():
    s = settings.DB_SCHEMA
    op.add_column(
        "outbox_messages",
        sa.Column("reply_evidence_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema=s,
    )
    op.create_table(
        "outbox_reply_evidence",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("outbox_message_id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("inbound_message_id", sa.Integer(), nullable=False),
        sa.Column("headers_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("pii_updated_at", sa.BigInteger(), nullable=False, server_default="1"),
        sa.UniqueConstraint("tenant_id", "outbox_message_id", name="uq_outbox_reply_evidence_message"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "outbox_message_id"],
            [f"{s}.outbox_messages.tenant_id", f"{s}.outbox_messages.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "thread_id"],
            [f"{s}.message_threads.tenant_id", f"{s}.message_threads.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "inbound_message_id"],
            [f"{s}.unified_messages.tenant_id", f"{s}.unified_messages.id"],
            ondelete="CASCADE",
        ),
        schema=s,
    )
    op.create_index("ix_outbox_reply_evidence_thread", "outbox_reply_evidence", ["tenant_id", "thread_id"], schema=s)
    op.create_index(
        "ix_outbox_reply_evidence_inbound", "outbox_reply_evidence", ["tenant_id", "inbound_message_id"], schema=s
    )
    quoted_schema = op.get_bind().dialect.identifier_preparer.quote_schema(s)
    op.execute(
        f"CREATE TRIGGER contact_pii_marker BEFORE INSERT OR UPDATE ON {quoted_schema}.outbox_reply_evidence "
        f"FOR EACH ROW EXECUTE FUNCTION {quoted_schema}.bump_contact_pii_marker()"
    )


def downgrade():
    if context.is_offline_mode():
        raise RuntimeError("Cannot safely downgrade reply evidence offline")
    s = settings.DB_SCHEMA
    connection = op.get_bind()
    quoted_schema = connection.dialect.identifier_preparer.quote_schema(s)
    # Exclude inserts and flag changes until downgrade commits or rolls back.
    connection.execute(sa.text(f"LOCK TABLE {quoted_schema}.outbox_messages IN ACCESS EXCLUSIVE MODE"))
    connection.execute(sa.text(f"LOCK TABLE {quoted_schema}.outbox_reply_evidence IN ACCESS EXCLUSIVE MODE"))
    evidence = connection.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {quoted_schema}.outbox_reply_evidence)"))
    replies = connection.scalar(
        sa.text(f"SELECT EXISTS (SELECT 1 FROM {quoted_schema}.outbox_messages WHERE reply_evidence_required)")
    )
    if evidence or replies:
        raise RuntimeError("Downgrade refused: durable reply evidence or reply-required outbox items exist")
    op.drop_index("ix_outbox_reply_evidence_inbound", table_name="outbox_reply_evidence", schema=s)
    op.drop_index("ix_outbox_reply_evidence_thread", table_name="outbox_reply_evidence", schema=s)
    op.drop_table("outbox_reply_evidence", schema=s)
    op.drop_column("outbox_messages", "reply_evidence_required", schema=s)
