"""P17-T01 workspace-level outbound destination approvals.

Revision ID: p17t01_workspace_hosts
Revises: 0152
The task revision is a placeholder; rebase and assign the final number at merge.
"""

import sqlalchemy as sa
from alembic import context, op

from app.core.config import settings

HOST_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"

revision = "p17t01_workspace_hosts"
down_revision = "0152"
branch_labels = None
depends_on = None


def upgrade():
    schema = settings.DB_SCHEMA
    op.create_table(
        "workspace_outbound_hosts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{schema}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column(
            "approved_by_user_id",
            sa.Integer(),
            sa.ForeignKey(f"{schema}.users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "host", name="uq_workspace_outbound_hosts_tenant_host"),
        sa.CheckConstraint(f"host ~ '{HOST_PATTERN}'", name="ck_workspace_outbound_hosts_host"),
        schema=schema,
    )


def downgrade():
    if context.is_offline_mode():
        raise RuntimeError("Cannot safely downgrade workspace host approvals offline")
    schema = settings.DB_SCHEMA
    connection = op.get_bind()
    quoted = connection.dialect.identifier_preparer.quote_schema(schema)
    connection.execute(sa.text(f"LOCK TABLE {quoted}.workspace_outbound_hosts IN ACCESS EXCLUSIVE MODE"))
    if connection.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {quoted}.workspace_outbound_hosts)")):
        raise RuntimeError("Downgrade refused: workspace host approvals exist")
    op.drop_table("workspace_outbound_hosts", schema=schema)
