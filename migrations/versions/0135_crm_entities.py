"""P14-T02: read-only tenant-scoped CRM entities (`crm_entities`) and PII rotation marker.

Revision ID: 0135
Revises: 0134
No automatic contact backfill or cross-source deduplication during schema upgrade.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0135"
down_revision = "0134"
branch_labels = None
depends_on = None

CRM_PROVIDERS = "'bitrix24'"
CRM_ENTITY_KINDS = "'contact', 'company', 'lead', 'deal'"


def upgrade() -> None:
    s = settings.DB_SCHEMA
    op.create_table(
        "crm_entities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("pii_updated_at", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False, server_default="bitrix24"),
        sa.Column("entity_kind", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=True),
        sa.Column("company_external_id", sa.String(64), nullable=True),
        sa.Column("stage_code", sa.String(80), nullable=True),
        sa.Column("status_code", sa.String(80), nullable=True),
        sa.Column("currency_code", sa.String(12), nullable=True),
        sa.Column("opportunity_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("assigned_by_external_id", sa.String(64), nullable=True),
        sa.Column("is_closed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_deleted_remote", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title_encrypted", sa.String(), nullable=False),
        sa.Column("personal_fields_encrypted", sa.String(), nullable=True),
        sa.Column("custom_fields_encrypted", sa.String(), nullable=True),
        sa.UniqueConstraint("tenant_id", "id", name="uq_crm_entities_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "connection_id",
            "entity_kind",
            "external_id",
            name="uq_crm_entities_connection_external",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            [f"{s}.workspace_connections.tenant_id", f"{s}.workspace_connections.id"],
            ondelete="CASCADE",
            name="fk_crm_entities_connection",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            [f"{s}.contacts.tenant_id", f"{s}.contacts.id"],
            ondelete="SET NULL",
            name="fk_crm_entities_contact",
        ),
        sa.CheckConstraint(f"provider IN ({CRM_PROVIDERS})", name="ck_crm_entities_provider"),
        sa.CheckConstraint(f"entity_kind IN ({CRM_ENTITY_KINDS})", name="ck_crm_entities_kind"),
        schema=s,
    )
    op.create_index("ix_crm_entities_tenant_kind", "crm_entities", ["tenant_id", "entity_kind"], schema=s)
    op.create_index("ix_crm_entities_tenant_contact", "crm_entities", ["tenant_id", "contact_id"], schema=s)

    schema = op.get_bind().dialect.identifier_preparer.quote_schema(s)
    op.execute(
        f"""
        CREATE TRIGGER contact_pii_marker BEFORE INSERT OR UPDATE ON {schema}.crm_entities
        FOR EACH ROW EXECUTE FUNCTION {schema}.bump_contact_pii_marker()
        """
    )


def downgrade() -> None:
    s = settings.DB_SCHEMA
    op.drop_table("crm_entities", schema=s)
