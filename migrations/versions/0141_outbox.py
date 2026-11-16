"""P15-T05 encrypted transport outbox and durable one-attempt delivery fences.

The revision owns the outbox tables and their central catalog entry. The
catalog entry is seeded here rather than by the generic post-migration model
scanner, so the mapped ``outbox_messages`` table cannot acquire a second,
uncatalogued model type.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0141"
down_revision = "0140"
branch_labels = None
depends_on = None

WORKSPACE_ROLE_PRESETS = {
    "owner": ("VIEW", "UPDATE", "CONFIGURE"),
    "admin": ("VIEW", "UPDATE", "CONFIGURE"),
    "manager": ("VIEW", "UPDATE"),
    "analyst": ("VIEW",),
    "viewer": ("VIEW",),
}


def _timestamps():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("pii_updated_at", sa.BigInteger(), nullable=False, server_default="1"),
    )


def _registry(add: bool) -> None:
    """Seed or remove the single durable ``comms.outbox`` catalog identity."""

    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote_schema(settings.DB_SCHEMA)
    resource_key = "comms.outbox"
    if add:
        # resource_key is unique after the access-catalog transition. First
        # promote a legacy generic row for this table when one exists; the
        # table-name predicate then prevents a second row on re-entry.
        bind.execute(
            sa.text(f"""
                UPDATE {schema}.model_types
                SET resource_key = :resource_key,
                    model_name = 'outboxmessage',
                    description = 'Persistent communication outbox',
                    is_managed = true,
                    updated_at = now()
                WHERE table_name = 'outbox_messages'
                  AND resource_key IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM {schema}.model_types WHERE resource_key = :resource_key
                  )
                """),
            {"resource_key": resource_key},
        )
        bind.execute(
            sa.text(f"""
                INSERT INTO {schema}.model_types
                    (resource_key, model_name, table_name, description, is_managed, created_at, updated_at)
                SELECT CAST(:resource_key AS varchar), 'outboxmessage', 'outbox_messages',
                       'Persistent communication outbox', true, now(), now()
                WHERE NOT EXISTS (
                    SELECT 1 FROM {schema}.model_types
                    WHERE resource_key = :resource_key OR table_name = 'outbox_messages'
                )
                ON CONFLICT (resource_key) DO NOTHING
                """),
            {"resource_key": resource_key},
        )
        for action in ("VIEW", "UPDATE", "CONFIGURE"):
            bind.execute(
                sa.text(f"""
                    INSERT INTO {schema}.permissions (name, action_type, model_type_id, created_at, updated_at)
                    SELECT CAST(:name AS varchar), CAST(:action AS {schema}.action_type), id, now(), now()
                    FROM {schema}.model_types
                    WHERE resource_key = :resource_key
                    ON CONFLICT (model_type_id, action_type) DO NOTHING
                    """),
                {"name": f"Outbox: {action.lower()}", "action": action, "resource_key": resource_key},
            )
        for codename, actions in WORKSPACE_ROLE_PRESETS.items():
            for action in actions:
                bind.execute(
                    sa.text(f"""
                        INSERT INTO {schema}.workspace_role_permission (role_id, permission_id)
                        SELECT wr.id, p.id
                        FROM {schema}.workspace_roles wr
                        CROSS JOIN {schema}.permissions p
                        JOIN {schema}.model_types m ON p.model_type_id = m.id
                        WHERE wr.codename = :codename
                          AND m.resource_key = :resource_key
                          AND p.action_type = CAST(:action AS {schema}.action_type)
                        ON CONFLICT (role_id, permission_id) DO NOTHING
                        """),
                    {"codename": codename, "resource_key": resource_key, "action": action},
                )
        return

    for link in ("role_permission", "workspace_role_permission"):
        bind.execute(
            sa.text(f"""
                DELETE FROM {schema}.{link} l USING {schema}.permissions p, {schema}.model_types m
                WHERE l.permission_id = p.id
                  AND p.model_type_id = m.id
                  AND m.resource_key = :resource_key
                """),
            {"resource_key": resource_key},
        )
    bind.execute(
        sa.text(f"""
            DELETE FROM {schema}.permissions p USING {schema}.model_types m
            WHERE p.model_type_id = m.id AND m.resource_key = :resource_key
            """),
        {"resource_key": resource_key},
    )
    bind.execute(
        sa.text(f"DELETE FROM {schema}.model_types WHERE resource_key = :resource_key"),
        {"resource_key": resource_key},
    )


def upgrade() -> None:
    s = settings.DB_SCHEMA
    states = "'draft','validated','approved','claimed','sent','uncertain_delivery','failed','cancelled'"
    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_id", sa.Integer(), sa.ForeignKey(f"{s}.users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(71), nullable=False),
        sa.Column("subject_encrypted", sa.String(), nullable=True),
        sa.Column("content_encrypted", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.Integer(), sa.ForeignKey(f"{s}.users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("approved_hash", sa.String(71), nullable=True),
        sa.Column("approval_expires_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_outbox_message_tenant"),
        sa.UniqueConstraint("tenant_id", "author_id", "idempotency_key", name="uq_outbox_message_request"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id"], [f"{s}.contacts.tenant_id", f"{s}.contacts.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(f"state IN ({states})", name="ck_outbox_message_state"),
        sa.CheckConstraint(
            "state NOT IN ('approved','claimed','sent','uncertain_delivery') OR (approved_by IS NOT NULL AND approved_hash IS NOT NULL AND approval_expires_at IS NOT NULL AND scheduled_at IS NOT NULL)",
            name="ck_outbox_message_approval",
        ),
        sa.CheckConstraint("purpose IN ('transactional','greeting','marketing')", name="ck_outbox_purpose"),
        schema=s,
    )
    op.create_index("ix_outbox_due", "outbox_messages", ["tenant_id", "state", "scheduled_at", "id"], schema=s)
    op.create_table(
        "outbox_targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("identity_id", sa.Integer(), nullable=False),
        sa.Column("recipient_encrypted", sa.String(), nullable=False),
        sa.Column("credential_hash", sa.String(71), nullable=False),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column("command_id", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dispatch_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", sa.String(36), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_outbox_target_tenant"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_outbox_target_attempt"),
        sa.UniqueConstraint(
            "tenant_id", "message_id", "channel", "connection_id", "identity_id", name="uq_outbox_destination"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "message_id"],
            [f"{s}.outbox_messages.tenant_id", f"{s}.outbox_messages.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            [f"{s}.workspace_connections.tenant_id", f"{s}.workspace_connections.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "command_id"],
            [f"{s}.business_commands.tenant_id", f"{s}.business_commands.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(f"state IN ({states})", name="ck_outbox_target_state"),
        sa.CheckConstraint("channel IN ('email','telegram','max')", name="ck_outbox_target_channel"),
        sa.CheckConstraint(
            "state NOT IN ('claimed','sent','uncertain_delivery') OR attempts = 1", name="ck_outbox_attempt_state"
        ),
        sa.CheckConstraint("state != 'approved' OR dispatch_after IS NOT NULL", name="ck_outbox_approved_due"),
        sa.CheckConstraint("attempts IN (0,1)", name="ck_outbox_single_attempt"),
        sa.CheckConstraint(
            "(attempts = 0 AND claim_token IS NULL AND claimed_at IS NULL) OR (attempts = 1 AND claim_token IS NOT NULL AND claimed_at IS NOT NULL)",
            name="ck_outbox_claim_fence",
        ),
        sa.CheckConstraint("delivery_started_at IS NULL OR attempts = 1", name="ck_outbox_delivery_started"),
        schema=s,
    )
    op.create_index("ix_outbox_target_message", "outbox_targets", ["tenant_id", "message_id", "state"], schema=s)
    schema = op.get_bind().dialect.identifier_preparer.quote_schema(s)
    for table in ("outbox_messages", "outbox_targets"):
        op.execute(
            f"CREATE TRIGGER contact_pii_marker BEFORE INSERT OR UPDATE ON {schema}.{table} FOR EACH ROW EXECUTE FUNCTION {schema}.bump_contact_pii_marker()"
        )
    _registry(add=True)


def downgrade() -> None:
    _registry(add=False)
    # PostgreSQL drops owned triggers, constraints, indexes and sequences with
    # each table.
    op.drop_table("outbox_targets", schema=settings.DB_SCHEMA)
    op.drop_table("outbox_messages", schema=settings.DB_SCHEMA)
