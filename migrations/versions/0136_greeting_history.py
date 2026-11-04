"""P14-T04: tenant-scoped greeting history and AI greeting drafts (`greeting_history`).

Revision ID: 0136
Revises: 0135
Enforces unique `(tenant_id, contact_id, event_type, year)` to prevent repeat greetings
and attaches the `contact_pii_marker` trigger for PII rotation safety.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0136"
down_revision = "0135"
branch_labels = None
depends_on = None

GREETING_EVENT_TYPES = "'birthday', 'anniversary'"
GREETING_STATUSES = "'draft', 'approved', 'sent', 'failed', 'skipped', 'unknown'"
GREETING_CHANNELS = "'email', 'phone', 'telegram', 'max', 'bitrix24'"


def upgrade() -> None:
    s = settings.DB_SCHEMA
    op.create_table(
        "greeting_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("pii_updated_at", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(20), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("ai_generated", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("command_id", sa.Integer(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("subject_encrypted", sa.String(), nullable=True),
        sa.Column("draft_text_encrypted", sa.String(), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_greeting_history_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "contact_id",
            "event_type",
            "year",
            name="uq_greeting_history_contact_event_year",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            [f"{s}.contacts.tenant_id", f"{s}.contacts.id"],
            ondelete="CASCADE",
            name="fk_greeting_history_contact",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "command_id"],
            [f"{s}.business_commands.tenant_id", f"{s}.business_commands.id"],
            ondelete="SET NULL",
            name="fk_greeting_history_command",
        ),
        sa.CheckConstraint(f"event_type IN ({GREETING_EVENT_TYPES})", name="ck_greeting_history_event_type"),
        sa.CheckConstraint(f"status IN ({GREETING_STATUSES})", name="ck_greeting_history_status"),
        sa.CheckConstraint(
            f"channel IS NULL OR channel IN ({GREETING_CHANNELS})",
            name="ck_greeting_history_channel",
        ),
        sa.CheckConstraint("year >= 1970 AND year <= 2100", name="ck_greeting_history_year"),
        schema=s,
    )
    op.create_index(
        "ix_greeting_history_tenant_year_status",
        "greeting_history",
        ["tenant_id", "year", "status"],
        schema=s,
    )

    schema = op.get_bind().dialect.identifier_preparer.quote_schema(s)
    op.execute(
        f"""
        CREATE TRIGGER contact_pii_marker BEFORE INSERT OR UPDATE ON {schema}.greeting_history
        FOR EACH ROW EXECUTE FUNCTION {schema}.bump_contact_pii_marker()
        """
    )


def downgrade() -> None:
    s = settings.DB_SCHEMA
    op.drop_table("greeting_history", schema=s)
