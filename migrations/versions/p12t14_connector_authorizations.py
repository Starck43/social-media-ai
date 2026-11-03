"""Personal authorization reuse with explicit workspace consent; no credential import.

Revision ID: p12t14_accounts
Revises: 0123

This draft branch is independent of the unmerged VK 0124 migration. Reconcile the
Alembic graph during reviewed integration; do not deploy multiple unmerged heads.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "p12t14_accounts"
down_revision = "0123"
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
        "personal_connector_authorizations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_user_id", sa.Integer(), sa.ForeignKey(f"{schema}.users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("provider_id", sa.String(50), nullable=False),
        sa.Column("auth_method", sa.String(30), nullable=False),
        sa.Column("subject_key", sa.String(320), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="active"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "owner_user_id", "provider_id", "auth_method", "subject_key", name="uq_personal_connector_subject"
        ),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_personal_connector_owner"),
        sa.CheckConstraint("state IN ('active','reauth','revoked')", name="ck_personal_connector_state"),
        sa.CheckConstraint("revision > 0", name="ck_personal_connector_revision"),
        schema=schema,
    )
    op.create_table(
        "workspace_authorization_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{schema}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("authorization_id", sa.Integer(), nullable=False),
        sa.Column("authorization_revision", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["authorization_id", "owner_user_id"],
            [
                f"{schema}.personal_connector_authorizations.id",
                f"{schema}.personal_connector_authorizations.owner_user_id",
            ],
            ondelete="CASCADE",
            name="fk_workspace_authorization_owner",
        ),
        sa.UniqueConstraint("tenant_id", "authorization_id", name="uq_workspace_authorization_grant"),
        sa.CheckConstraint("authorization_revision > 0", name="ck_workspace_authorization_revision"),
        schema=schema,
    )


def downgrade():
    # Destructive rollback of NEW authorizations/grants only. Securely back up
    # approved retained data first. Never touch/import the legacy vault.
    op.drop_table("workspace_authorization_grants", schema=settings.DB_SCHEMA)
    op.drop_table("personal_connector_authorizations", schema=settings.DB_SCHEMA)
