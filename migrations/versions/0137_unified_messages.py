"""Tenant-scoped unified communication hub tables (`message_threads`, `unified_messages`, `contact_channel_stats`, P15-T02).

Revision ID: 0137
Revises: 0138
Owned by P15-T02 for the P15-T01..T04 inbox stack. Metadata-only per D3 and D11:
no message bodies, no attachments, and no full-text search index. Registers
`comms.message` (`VIEW CREATE UPDATE DELETE`) per D14.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0137"
down_revision = "0138"
branch_labels = None
depends_on = None

CHANNELS = "'email', 'telegram', 'max', 'phone', 'bitrix24'"
DIRECTIONS = "'inbound', 'outbound'"
PII_TABLES = ("message_threads", "unified_messages")
ALL_TABLES = ("message_threads", "unified_messages", "contact_channel_stats")


def _registry(add: bool) -> None:
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote_schema(settings.DB_SCHEMA)
    resource_key = "comms.message"
    if add:
        bind.execute(
            sa.text(
                f"""
                INSERT INTO {schema}.model_types
                    (resource_key, model_name, table_name, description, is_managed, created_at, updated_at)
                VALUES (:resource_key, 'unifiedmessage', 'unified_messages',
                        'Tenant unified communication message metadata', true, now(), now())
                ON CONFLICT (resource_key) DO NOTHING
                """
            ),
            {"resource_key": resource_key},
        )
        for action in ("VIEW", "CREATE", "UPDATE", "DELETE"):
            bind.execute(
                sa.text(
                    f"""
                    INSERT INTO {schema}.permissions (name, action_type, model_type_id, created_at, updated_at)
                    SELECT :name, CAST(:action AS {schema}.action_type), id, now(), now()
                    FROM {schema}.model_types WHERE resource_key = :resource_key
                    ON CONFLICT (model_type_id, action_type) DO NOTHING
                    """
                ),
                {"name": "Unified message: " + action.lower(), "action": action, "resource_key": resource_key},
            )
        return

    for link in ("role_permission", "workspace_role_permission"):
        bind.execute(
            sa.text(
                f"""
                DELETE FROM {schema}.{link} l USING {schema}.permissions p, {schema}.model_types m
                WHERE l.permission_id = p.id AND p.model_type_id = m.id AND m.resource_key = :resource_key
                """
            ),
            {"resource_key": resource_key},
        )
    bind.execute(
        sa.text(
            f"""
            DELETE FROM {schema}.permissions p USING {schema}.model_types m
            WHERE p.model_type_id = m.id AND m.resource_key = :resource_key
            """
        ),
        {"resource_key": resource_key},
    )
    bind.execute(
        sa.text(f"DELETE FROM {schema}.model_types WHERE resource_key = :resource_key"),
        {"resource_key": resource_key},
    )


def upgrade() -> None:
    s = settings.DB_SCHEMA
    op.create_table(
        "message_threads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("pii_updated_at", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column("contact_id", sa.Integer(), nullable=True),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("thread_key", sa.String(255), nullable=False),
        sa.Column("subject_encrypted", sa.String(), nullable=True),
        sa.Column("participants_encrypted", sa.String(), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unread_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("tenant_id", "id", name="uq_message_threads_tenant_id"),
        sa.UniqueConstraint("tenant_id", "connection_id", "channel", "thread_key", name="uq_message_threads_key"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            [f"{s}.workspace_connections.tenant_id", f"{s}.workspace_connections.id"],
            ondelete="CASCADE",
            name="fk_message_threads_connection",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            [f"{s}.contacts.tenant_id", f"{s}.contacts.id"],
            ondelete="SET NULL",
            name="fk_message_threads_contact",
        ),
        sa.CheckConstraint(f"channel IN ({CHANNELS})", name="ck_message_threads_channel"),
        sa.CheckConstraint("message_count >= 0", name="ck_message_threads_message_count"),
        sa.CheckConstraint("unread_count >= 0", name="ck_message_threads_unread_count"),
        schema=s,
    )
    op.create_index(
        "ix_message_threads_tenant_last_msg",
        "message_threads",
        ["tenant_id", "last_message_at", "id"],
        schema=s,
    )
    op.create_index("ix_message_threads_tenant_contact", "message_threads", ["tenant_id", "contact_id"], schema=s)

    op.create_table(
        "unified_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("pii_updated_at", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=True),
        sa.Column("thread_id", sa.Integer(), nullable=True),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("original_id", sa.String(255), nullable=False),
        sa.Column("external_message_id_hash", sa.String(64), nullable=True),
        sa.Column("in_reply_to_hash", sa.String(64), nullable=True),
        sa.Column("direction", sa.String(20), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subject_encrypted", sa.String(), nullable=True),
        sa.Column("sender_encrypted", sa.String(), nullable=True),
        sa.Column("recipient_encrypted", sa.String(), nullable=True),
        sa.Column("original_url", sa.String(1024), nullable=True),
        sa.Column("content_hash", sa.String(71), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("unavailable_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "id", name="uq_unified_messages_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "connection_id",
            "channel",
            "original_id",
            name="uq_unified_messages_origin",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            [f"{s}.workspace_connections.tenant_id", f"{s}.workspace_connections.id"],
            ondelete="CASCADE",
            name="fk_unified_messages_connection",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            [f"{s}.contacts.tenant_id", f"{s}.contacts.id"],
            ondelete="SET NULL",
            name="fk_unified_messages_contact",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "thread_id"],
            [f"{s}.message_threads.tenant_id", f"{s}.message_threads.id"],
            ondelete="SET NULL",
            name="fk_unified_messages_thread",
        ),
        sa.CheckConstraint(f"channel IN ({CHANNELS})", name="ck_unified_messages_channel"),
        sa.CheckConstraint(f"direction IN ({DIRECTIONS})", name="ck_unified_messages_direction"),
        schema=s,
    )
    op.create_index(
        "ix_unified_messages_tenant_thread_sent",
        "unified_messages",
        ["tenant_id", "thread_id", "sent_at", "id"],
        schema=s,
    )
    op.create_index(
        "ix_unified_messages_tenant_contact_sent",
        "unified_messages",
        ["tenant_id", "contact_id", "sent_at", "id"],
        schema=s,
    )
    op.create_index(
        "ix_unified_messages_tenant_msgid_hash",
        "unified_messages",
        ["tenant_id", "connection_id", "external_message_id_hash"],
        schema=s,
    )

    op.create_table(
        "contact_channel_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("inbound_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("outbound_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_outbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "id", name="uq_contact_channel_stats_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "contact_id",
            "channel",
            name="uq_contact_channel_stats_contact_channel",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            [f"{s}.contacts.tenant_id", f"{s}.contacts.id"],
            ondelete="CASCADE",
            name="fk_contact_channel_stats_contact",
        ),
        sa.CheckConstraint(f"channel IN ({CHANNELS})", name="ck_contact_channel_stats_channel"),
        sa.CheckConstraint("inbound_count >= 0", name="ck_contact_channel_stats_inbound_nonneg"),
        sa.CheckConstraint("outbound_count >= 0", name="ck_contact_channel_stats_outbound_nonneg"),
        schema=s,
    )
    op.create_index(
        "ix_contact_channel_stats_tenant_contact",
        "contact_channel_stats",
        ["tenant_id", "contact_id"],
        schema=s,
    )

    schema = op.get_bind().dialect.identifier_preparer.quote_schema(s)
    for table in PII_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER contact_pii_marker BEFORE INSERT OR UPDATE ON {schema}.{table}
            FOR EACH ROW EXECUTE FUNCTION {schema}.bump_contact_pii_marker()
        """
        )
    _registry(add=True)


def downgrade() -> None:
    s = settings.DB_SCHEMA
    _registry(add=False)
    for table in ("contact_channel_stats", "unified_messages", "message_threads"):
        op.drop_table(table, schema=s)
