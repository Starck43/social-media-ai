"""Bind explicit same-subject reauthorization before any provider operation.

Revision ID: p12t14_tg_recovery
Revises: p12t14_tg_runtime
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "p12t14_tg_recovery"
down_revision = "p12t14_tg_runtime"
branch_labels = None
depends_on = None


def upgrade():
    schema = settings.DB_SCHEMA
    op.add_column("telegram_login_attempts", sa.Column("recovery_authorization_id", sa.Integer), schema=schema)
    op.add_column("telegram_login_attempts", sa.Column("recovery_revision", sa.Integer), schema=schema)
    op.create_foreign_key(
        "fk_telegram_login_recovery",
        "telegram_login_attempts",
        "personal_connector_authorizations",
        ["recovery_authorization_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_telegram_login_recovery_pin",
        "telegram_login_attempts",
        "(recovery_authorization_id IS NULL AND recovery_revision IS NULL) OR "
        "(recovery_authorization_id IS NOT NULL AND recovery_revision IS NOT NULL AND recovery_revision > 0)",
        schema=schema,
    )


def downgrade():
    # Stop/drain login and runtime writers first. Removing the pin while an old
    # provider result can still settle would allow recovery to become creation.
    op.drop_constraint("ck_telegram_login_recovery_pin", "telegram_login_attempts", schema=settings.DB_SCHEMA)
    op.drop_constraint("fk_telegram_login_recovery", "telegram_login_attempts", schema=settings.DB_SCHEMA)
    op.drop_column("telegram_login_attempts", "recovery_revision", schema=settings.DB_SCHEMA)
    op.drop_column("telegram_login_attempts", "recovery_authorization_id", schema=settings.DB_SCHEMA)
