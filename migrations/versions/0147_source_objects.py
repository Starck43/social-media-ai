"""Introduce shared identity when the second calendar consumer arrives (P16-T04).

Revision ID: 0147
Revises: 0146
Identity is moved, never duplicated. Downgrade restores the module-scoped key.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0147"
down_revision = "0146"
branch_labels = depends_on = None


def upgrade():
    s = settings.DB_SCHEMA
    op.create_table(
        "source_objects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(1500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "connection_id", "external_id", name="uq_source_object_identity"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_source_object_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            [f"{s}.workspace_connections.tenant_id", f"{s}.workspace_connections.id"],
            ondelete="CASCADE",
        ),
        schema=s,
    )
    # SQL identifiers come from validated application schema settings, not input.
    q = op.get_bind().dialect.identifier_preparer.quote_schema(s)
    op.execute(
        sa.text(
            f"INSERT INTO {q}.source_objects (tenant_id, connection_id, external_id) SELECT tenant_id, connection_id, external_id FROM {q}.calendar_events"
        )
    )
    op.add_column("calendar_events", sa.Column("source_object_id", sa.Integer()), schema=s)
    op.execute(
        sa.text(
            f"UPDATE {q}.calendar_events e SET source_object_id = o.id FROM {q}.source_objects o WHERE (e.tenant_id,e.connection_id,e.external_id) = (o.tenant_id,o.connection_id,o.external_id)"
        )
    )
    op.alter_column("calendar_events", "source_object_id", nullable=False, schema=s)
    op.create_foreign_key(
        "fk_calendar_event_source",
        "calendar_events",
        "source_objects",
        ["tenant_id", "source_object_id"],
        ["tenant_id", "id"],
        source_schema=s,
        referent_schema=s,
        ondelete="CASCADE",
    )
    op.create_unique_constraint("uq_calendar_event_source", "calendar_events", ["source_object_id"], schema=s)
    op.drop_constraint("fk_calendar_event_connection", "calendar_events", schema=s, type_="foreignkey")
    op.drop_constraint("uq_calendar_event_identity", "calendar_events", schema=s, type_="unique")
    op.drop_column("calendar_events", "external_id", schema=s)
    op.drop_column("calendar_events", "connection_id", schema=s)


def downgrade():
    s = settings.DB_SCHEMA
    q = op.get_bind().dialect.identifier_preparer.quote_schema(s)
    op.add_column("calendar_events", sa.Column("connection_id", sa.Integer()), schema=s)
    op.add_column("calendar_events", sa.Column("external_id", sa.String(1500)), schema=s)
    op.execute(
        sa.text(
            f"UPDATE {q}.calendar_events e SET connection_id = o.connection_id, external_id = o.external_id FROM {q}.source_objects o WHERE e.source_object_id = o.id AND e.tenant_id = o.tenant_id"
        )
    )
    for col in ("connection_id", "external_id"):
        op.alter_column("calendar_events", col, nullable=False, schema=s)
    op.create_unique_constraint(
        "uq_calendar_event_identity", "calendar_events", ["tenant_id", "connection_id", "external_id"], schema=s
    )
    op.create_foreign_key(
        "fk_calendar_event_connection",
        "calendar_events",
        "workspace_connections",
        ["tenant_id", "connection_id"],
        ["tenant_id", "id"],
        source_schema=s,
        referent_schema=s,
        ondelete="CASCADE",
    )
    op.drop_constraint("fk_calendar_event_source", "calendar_events", schema=s, type_="foreignkey")
    op.drop_constraint("uq_calendar_event_source", "calendar_events", schema=s, type_="unique")
    op.drop_column("calendar_events", "source_object_id", schema=s)
    op.drop_table("source_objects", schema=s)
