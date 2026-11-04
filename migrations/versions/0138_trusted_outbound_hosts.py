"""P14-T09: installation-wide trusted outbound hosts and their change ledger.

Revision ID: 0138
Revises: 0136

Both installation-level tables have no tenant_id by the owner-authorized exception
of 2026-11-04. Downgrade drops the ledger only after the current registry is empty.

Ordering note: P15-T02 owns `0137`. If `0137` lands on `dev` first, this file's
`down_revision` becomes `"0137"` (this file only; never edit another revision).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import settings

revision = "0138"
down_revision = "0136"
branch_labels = None
depends_on = None

HOST_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"


def upgrade() -> None:
    s = settings.DB_SCHEMA
    op.create_table(
        "trusted_outbound_hosts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "authorized_addresses",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("note", sa.String(200), nullable=False, server_default=""),
        sa.Column(
            "created_by_actor_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("host", name="uq_trusted_outbound_hosts_host"),
        sa.CheckConstraint(f"host ~ '{HOST_PATTERN}'", name="ck_trusted_outbound_hosts_host"),
        sa.CheckConstraint("kind IN ('public', 'private')", name="ck_trusted_outbound_hosts_kind"),
        sa.CheckConstraint(
            "jsonb_typeof(authorized_addresses) = 'array' AND ("
            "(kind = 'public' AND jsonb_array_length(authorized_addresses) = 0) OR "
            "(kind = 'private' AND jsonb_array_length(authorized_addresses) BETWEEN 1 AND 16))",
            name="ck_trusted_outbound_hosts_addresses",
        ),
        sa.CheckConstraint("length(note) <= 200", name="ck_trusted_outbound_hosts_note"),
        sa.CheckConstraint("created_by_actor_id > 0", name="ck_trusted_outbound_hosts_creator"),
        schema=s,
    )
    op.create_table(
        "trusted_outbound_host_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("host_entry_id", sa.Integer(), nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.UniqueConstraint("event_id", name="uq_trusted_outbound_host_events_event_id"),
        sa.CheckConstraint("event_id ~ '^[0-9a-f]{32}$'", name="ck_trusted_outbound_host_events_event_id"),
        sa.CheckConstraint(
            "action IN ('admin.trusted_host_add', 'admin.trusted_host_remove')",
            name="ck_trusted_outbound_host_events_action",
        ),
        sa.CheckConstraint("actor_id > 0", name="ck_trusted_outbound_host_events_actor"),
        sa.CheckConstraint("host_entry_id > 0", name="ck_trusted_outbound_host_events_entry"),
        sa.CheckConstraint("jsonb_typeof(snapshot) = 'object'", name="ck_trusted_outbound_host_events_snapshot"),
        schema=s,
    )


def downgrade() -> None:
    # Lock out concurrent adds between the current-truth check and DDL.
    bind = op.get_bind()
    schema = settings.DB_SCHEMA.replace('"', '""')
    bind.execute(sa.text(f'LOCK TABLE "{schema}"."trusted_outbound_hosts" IN ACCESS EXCLUSIVE MODE'))
    table = sa.table("trusted_outbound_hosts", sa.column("id"), schema=settings.DB_SCHEMA)
    if bind.execute(sa.select(sa.literal(1)).select_from(table).limit(1)).first():
        raise ValueError("trusted_host_downgrade_requires_empty_registry")
    # Owner-directed: historical installation events are discarded on downgrade.
    op.drop_table("trusted_outbound_host_events", schema=settings.DB_SCHEMA)
    op.drop_table("trusted_outbound_hosts", schema=settings.DB_SCHEMA)
