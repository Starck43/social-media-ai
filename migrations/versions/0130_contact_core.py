"""Tenant-scoped contact core, encrypted PII and default-absent consent.

Revision ID: 0130
Revises: 0129
No contact backfill, provider import, merge execution or delivery authorization.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0130"
down_revision = "0129"
branch_labels = None
depends_on = None

TABLES = ("contacts", "contact_identities", "contact_consents", "contact_merge_journal")
CHANNELS = "'email', 'phone', 'telegram', 'max', 'bitrix24'"


def _common():
    s = settings.DB_SCHEMA
    return [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("pii_updated_at", sa.BigInteger(), nullable=False, server_default="1"),
    ]


def _contact_fk(column="contact_id", ondelete="CASCADE"):
    s = settings.DB_SCHEMA
    return sa.ForeignKeyConstraint(
        ["tenant_id", column], [f"{s}.contacts.tenant_id", f"{s}.contacts.id"], ondelete=ondelete
    )


def _registry(add):
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote_schema(settings.DB_SCHEMA)
    if add:
        bind.execute(
            sa.text(
                f"""
            INSERT INTO {schema}.model_types
                (resource_key, model_name, table_name, description, is_managed, created_at, updated_at)
            VALUES ('contacts.contact', 'contact', 'contacts', 'Tenant contact aggregate', true, now(), now())
            ON CONFLICT (resource_key) DO NOTHING
        """
            )
        )
        for action in ("VIEW", "CREATE", "UPDATE", "DELETE", "EXPORT"):
            bind.execute(
                sa.text(
                    f"""
                INSERT INTO {schema}.permissions (name, action_type, model_type_id, created_at, updated_at)
                SELECT :name, CAST(:action AS {schema}.action_type), id, now(), now()
                FROM {schema}.model_types WHERE resource_key = 'contacts.contact'
                ON CONFLICT (model_type_id, action_type) DO NOTHING
            """
                ),
                {"name": "Contacts: " + action.lower(), "action": action},
            )
    else:
        for link in ("role_permission", "workspace_role_permission"):
            bind.execute(
                sa.text(
                    f"""
                DELETE FROM {schema}.{link} l USING {schema}.permissions p, {schema}.model_types m
                WHERE l.permission_id = p.id AND p.model_type_id = m.id AND m.resource_key = 'contacts.contact'
            """
                )
            )
        bind.execute(
            sa.text(
                f"""
            DELETE FROM {schema}.permissions p USING {schema}.model_types m
            WHERE p.model_type_id = m.id AND m.resource_key = 'contacts.contact'
        """
            )
        )
        bind.execute(sa.text(f"DELETE FROM {schema}.model_types WHERE resource_key = 'contacts.contact'"))


def upgrade():
    s = settings.DB_SCHEMA
    op.create_table(
        "contacts",
        *_common(),
        sa.Column("display_name_encrypted", sa.String(), nullable=False),
        sa.Column("notes_encrypted", sa.String()),
        sa.Column("birthday_encrypted", sa.String()),
        sa.Column("birthday_has_year", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("anniversary_encrypted", sa.String()),
        sa.Column("anniversary_has_year", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("preferred_channel", sa.String(20)),
        sa.Column("last_active_channel", sa.String(20)),
        sa.Column("last_active_at", sa.DateTime(timezone=True)),
        sa.Column("merged_into_id", sa.Integer()),
        sa.UniqueConstraint("tenant_id", "id", name="uq_contacts_tenant_id"),
        _contact_fk("merged_into_id", "NO ACTION"),
        sa.CheckConstraint("merged_into_id IS NULL OR merged_into_id != id", name="ck_contact_not_self_merged"),
        sa.CheckConstraint(f"preferred_channel IN ({CHANNELS})", name="ck_contact_preferred_channel"),
        sa.CheckConstraint(f"last_active_channel IN ({CHANNELS})", name="ck_contact_last_channel"),
        schema=s,
    )
    op.create_index("ix_contacts_tenant_id_id", "contacts", ["tenant_id", "id"], schema=s)
    op.create_table(
        "contact_identities",
        *_common(),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("connection_id", sa.Integer()),
        sa.Column("value_encrypted", sa.String(), nullable=False),
        sa.Column("blind_index", sa.String(128), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_native_id_encrypted", sa.String()),
        _contact_fk(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            [f"{s}.workspace_connections.tenant_id", f"{s}.workspace_connections.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "kind IN ('email', 'phone', 'telegram_id', 'telegram_username', 'max_id', "
            "'bitrix24_contact', 'bitrix24_company', 'bitrix24_lead')",
            name="ck_contact_identity_kind",
        ),
        sa.UniqueConstraint("tenant_id", "kind", "blind_index", "connection_id", name="uq_contact_identity_connection"),
        schema=s,
    )
    op.create_index(
        "uq_contact_identity_unbound",
        "contact_identities",
        ["tenant_id", "kind", "blind_index"],
        unique=True,
        postgresql_where=sa.text("connection_id IS NULL"),
        schema=s,
    )
    op.create_index("ix_contact_identity_contact", "contact_identities", ["tenant_id", "contact_id"], schema=s)
    op.create_table(
        "contact_consents",
        *_common(),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("basis_encrypted", sa.String(), nullable=False),
        sa.Column("source_evidence_encrypted", sa.String(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True)),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True)),
        _contact_fk(),
        sa.CheckConstraint(f"channel IN ({CHANNELS})", name="ck_contact_consent_channel"),
        sa.CheckConstraint("purpose IN ('transactional', 'greeting', 'marketing')", name="ck_contact_consent_purpose"),
        sa.CheckConstraint("granted_at IS NOT NULL OR withdrawn_at IS NOT NULL", name="ck_contact_consent_recorded"),
        sa.CheckConstraint(
            "withdrawn_at IS NULL OR granted_at IS NULL OR withdrawn_at >= granted_at", name="ck_contact_consent_order"
        ),
        schema=s,
    )
    op.create_index("ix_contact_consent_contact", "contact_consents", ["tenant_id", "contact_id"], schema=s)
    op.create_table(
        "contact_merge_journal",
        *_common(),
        sa.Column("source_contact_id", sa.Integer(), nullable=False),
        sa.Column("target_contact_id", sa.Integer(), nullable=False),
        sa.Column("reason_encrypted", sa.String()),
        _contact_fk("source_contact_id"),
        _contact_fk("target_contact_id"),
        sa.CheckConstraint("source_contact_id != target_contact_id", name="ck_contact_merge_distinct"),
        schema=s,
    )
    for side in ("source", "target"):
        op.create_index(
            f"ix_contact_merge_{side}", "contact_merge_journal", ["tenant_id", f"{side}_contact_id"], schema=s
        )
    schema = op.get_bind().dialect.identifier_preparer.quote_schema(s)
    # Database-side marker covers ORM, bulk, SQL and byte-identical rewrites.
    op.execute(
        f"""
        CREATE FUNCTION {schema}.bump_contact_pii_marker() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN NEW.pii_updated_at := 1;
            ELSE NEW.pii_updated_at := OLD.pii_updated_at + 1; END IF;
            RETURN NEW;
        END; $$
    """
    )
    for table in TABLES:
        op.execute(
            f"""
            CREATE TRIGGER contact_pii_marker BEFORE INSERT OR UPDATE ON {schema}.{table}
            FOR EACH ROW EXECUTE FUNCTION {schema}.bump_contact_pii_marker()
        """
        )
    _registry(True)


def downgrade():
    s = settings.DB_SCHEMA
    schema = op.get_bind().dialect.identifier_preparer.quote_schema(s)
    _registry(False)
    for table in reversed(TABLES):
        op.drop_table(table, schema=s)
    op.execute(f"DROP FUNCTION {schema}.bump_contact_pii_marker()")
