"""Bounded encrypted login attempts; no legacy data or live authorization.

Revision ID: p12t14_tg_login
Revises: p12t14_accounts
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "p12t14_tg_login"
down_revision = "p12t14_accounts"
branch_labels = None
depends_on = None


def upgrade():
    schema = settings.DB_SCHEMA
    op.create_table(
        "telegram_login_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.Integer, sa.ForeignKey(f"{schema}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.Integer, sa.ForeignKey(f"{schema}.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_key", sa.String(36), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
        sa.Column("code_attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("password_attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("secret_encrypted", sa.Text, nullable=False),
        sa.Column("failure_code", sa.String(40)),
        sa.Column(
            "authorization_id",
            sa.Integer,
            sa.ForeignKey(f"{schema}.personal_connector_authorizations.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "grant_id", sa.Integer, sa.ForeignKey(f"{schema}.workspace_authorization_grants.id", ondelete="SET NULL")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("owner_user_id", "request_key", name="uq_telegram_login_request"),
        sa.CheckConstraint(
            "state IN ('processing','code','password','completed','failed','expired','cancelled')",
            name="ck_telegram_login_state",
        ),
        sa.CheckConstraint(
            "revision > 0 AND code_attempts >= 0 AND password_attempts >= 0", name="ck_telegram_login_counters"
        ),
        schema=schema,
    )
    op.create_index(
        "uq_telegram_login_pending_owner",
        "telegram_login_attempts",
        ["owner_user_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('processing','code','password')"),
        schema=schema,
    )
    op.create_index(
        "ix_telegram_login_owner_created", "telegram_login_attempts", ["owner_user_id", "created_at"], schema=schema
    )


def downgrade():
    # Loses pending login state, never retained personal authorizations or grants.
    # Stop login writers first; users must restart interrupted logins afterward.
    op.drop_table("telegram_login_attempts", schema=settings.DB_SCHEMA)
