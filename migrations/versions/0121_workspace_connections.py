"""Add explicit workspace provider connections without migrating the personal vault.

Revision ID: 0121
Revises: 0120
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0121"
down_revision = "0120"
branch_labels = None
depends_on = None


def _timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def upgrade():
    schema = settings.DB_SCHEMA
    op.create_table(
        "workspace_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{schema}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "owner_user_id", sa.Integer(), sa.ForeignKey(f"{schema}.users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("provider_id", sa.String(50), nullable=False),
        sa.Column("auth_method", sa.String(30), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("account_key", sa.String(320), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("state", sa.String(20), nullable=False, server_default="unverified"),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.Column("check_code", sa.String(40)),
        *_timestamps(),
        sa.UniqueConstraint(
            "tenant_id",
            "owner_user_id",
            "provider_id",
            "auth_method",
            "account_key",
            name="uq_workspace_connection_account",
        ),
        sa.CheckConstraint(
            "state IN ('unverified','verified','error','reauth','disabled')", name="ck_workspace_connection_state"
        ),
        schema=schema,
    )
    op.create_table(
        "workspace_connection_secrets",
        sa.Column(
            "connection_id",
            sa.Integer(),
            sa.ForeignKey(f"{schema}.workspace_connections.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        *_timestamps(),
        schema=schema,
    )


def downgrade():
    # Explicit rollback destroys new connection configuration/secrets only.
    # Export/backup them securely before downgrading; personal credentials are untouched.
    op.drop_table("workspace_connection_secrets", schema=settings.DB_SCHEMA)
    op.drop_table("workspace_connections", schema=settings.DB_SCHEMA)
