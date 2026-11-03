"""Monotonic Telegram runtime fencing; no session reads or data migration.

Revision ID: p12t14_tg_runtime
Revises: p12t14_tg_login
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "p12t14_tg_runtime"
down_revision = "p12t14_tg_login"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "personal_connector_authorizations",
        sa.Column("execution_generation", sa.BigInteger, nullable=False, server_default="0"),
        schema=settings.DB_SCHEMA,
    )
    op.create_check_constraint(
        "ck_personal_connector_execution_generation",
        "personal_connector_authorizations",
        "execution_generation >= 0",
        schema=settings.DB_SCHEMA,
    )


def downgrade():
    # Operational prerequisite: stop/drain ALL runtime writers and lock holders,
    # not just upgraded workers. Re-upgrade resets the removed epoch: no old
    # in-flight execution may survive this downgrade. No provider/session reset.
    op.drop_constraint(
        "ck_personal_connector_execution_generation",
        "personal_connector_authorizations",
        type_="check",
        schema=settings.DB_SCHEMA,
    )
    op.drop_column("personal_connector_authorizations", "execution_generation", schema=settings.DB_SCHEMA)
