"""Persist one bounded folder scan per personal connection.

Revision ID: 0126
Revises: 0125
No provider calls, content storage or mutation permissions are added.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0126"
down_revision = "0125"
branch_labels = None
depends_on = None


def upgrade():
    schema = settings.DB_SCHEMA
    op.create_table(
        "file_catalog_scans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{schema}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey(f"{schema}.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scan_token", sa.String(32), nullable=False),
        sa.Column("folder", sa.String(2048), nullable=False),
        sa.Column("policy_key", sa.String(64), nullable=False),
        sa.Column("next_offset", sa.Integer(), nullable=False),
        sa.Column("page_size", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(32), nullable=True),
        sa.Column("lease_token", sa.String(32), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            [f"{schema}.workspace_connections.tenant_id", f"{schema}.workspace_connections.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "connection_id", name="uq_file_catalog_scan_connection"),
        sa.CheckConstraint("state IN ('partial','error','exhausted','limited')", name="ck_file_scan_state"),
        sa.CheckConstraint("next_offset >= 0 AND next_offset <= 1000000", name="ck_file_scan_offset"),
        sa.CheckConstraint("page_size >= 1 AND page_size <= 100", name="ck_file_scan_page_size"),
        sa.CheckConstraint("octet_length(folder) <= 2048", name="ck_file_scan_folder_size"),
        sa.CheckConstraint(
            "(lease_token IS NULL AND lease_until IS NULL) OR (lease_token IS NOT NULL AND lease_until IS NOT NULL)",
            name="ck_file_scan_lease",
        ),
        schema=schema,
    )


def downgrade():
    # Removes only scan checkpoints; saved catalog metadata remains intact.
    op.drop_table("file_catalog_scans", schema=settings.DB_SCHEMA)
