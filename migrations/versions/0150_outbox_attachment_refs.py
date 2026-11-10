"""P15-T14 outbox attachment references.

Revision ID: 0150
Revises: 0149

Stores a reference to transient spooled bytes, never the bytes and never a
path (owner decision D9a, 2026-11-10). The spool may purge the file before
dispatch; that is a recorded refusal at send time, not a missing row.
"""

import sqlalchemy as sa
from alembic import context, op

from app.core.config import settings

revision = "0150"
down_revision = "0149"
branch_labels = None
depends_on = None


def upgrade():
    s = settings.DB_SCHEMA
    op.create_table(
        "outbox_attachment_refs",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("outbox_message_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("mime", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("pii_updated_at", sa.BigInteger(), nullable=False, server_default="1"),
        sa.CheckConstraint("position >= 0", name="ck_outbox_attachment_position"),
        sa.CheckConstraint("size_bytes > 0", name="ck_outbox_attachment_size"),
        sa.CheckConstraint("char_length(sha256) = 64", name="ck_outbox_attachment_hash"),
        sa.UniqueConstraint("tenant_id", "outbox_message_id", "position", name="uq_outbox_attachment_slot"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "outbox_message_id"],
            [f"{s}.outbox_messages.tenant_id", f"{s}.outbox_messages.id"],
            ondelete="CASCADE",
        ),
        schema=s,
    )
    op.create_index(
        "ix_outbox_attachment_message", "outbox_attachment_refs", ["tenant_id", "outbox_message_id"], schema=s
    )


def downgrade():
    if context.is_offline_mode():
        raise RuntimeError("Cannot safely downgrade outbox attachment references offline")
    s = settings.DB_SCHEMA
    connection = op.get_bind()
    quoted_schema = connection.dialect.identifier_preparer.quote_schema(s)
    # Exclude inserts until downgrade commits or rolls back: a draft admitted
    # between the check and the drop would lose its attachment set silently.
    connection.execute(sa.text(f"LOCK TABLE {quoted_schema}.outbox_messages IN ACCESS EXCLUSIVE MODE"))
    connection.execute(sa.text(f"LOCK TABLE {quoted_schema}.outbox_attachment_refs IN ACCESS EXCLUSIVE MODE"))
    pending = connection.scalar(
        sa.text(
            f"SELECT EXISTS (SELECT 1 FROM {quoted_schema}.outbox_attachment_refs r "
            f"JOIN {quoted_schema}.outbox_messages m "
            f"ON m.tenant_id = r.tenant_id AND m.id = r.outbox_message_id "
            f"WHERE m.state IN ('draft','validated','approved','claimed'))"
        )
    )
    if pending:
        raise RuntimeError("Downgrade refused: undelivered outbox items still reference attachments")
    op.drop_index("ix_outbox_attachment_message", table_name="outbox_attachment_refs", schema=s)
    op.drop_table("outbox_attachment_refs", schema=s)
