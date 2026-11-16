"""Minimal replay tombstones permit bounded login metadata retention.

Revision ID: p12t14_tg_retention
Revises: p12t14_tg_recovery

No session/credential is read. Backfill non-secret owner/request UUID pairs before
any retention deletion. Existing raw request IDs already have unique constraints.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "p12t14_tg_retention"
down_revision = "p12t14_tg_recovery"
branch_labels = None
depends_on = None


def upgrade():
    schema = settings.DB_SCHEMA
    op.create_table(
        "telegram_login_request_keys",
        sa.Column(
            "owner_user_id", sa.Integer, sa.ForeignKey(f"{schema}.users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("request_key", sa.String(36), primary_key=True),
        schema=schema,
    )
    # Core table expressions quote schema/identifiers through the dialect. Never
    # interpolate a schema from settings into SQL, even for non-secret metadata.
    keys = sa.table("telegram_login_request_keys", sa.column("owner_user_id"), sa.column("request_key"), schema=schema)
    attempts = sa.table("telegram_login_attempts", sa.column("owner_user_id"), sa.column("request_key"), schema=schema)
    op.execute(
        keys.insert().from_select(
            ["owner_user_id", "request_key"], sa.select(attempts.c.owner_user_id, attempts.c.request_key)
        )
    )
    op.create_index("ix_telegram_login_retention", "telegram_login_attempts", ["state", "created_at"], schema=schema)


def downgrade():
    # A tombstone may be the ONLY evidence after retention removed its attempt.
    # Never erase it as a routine rollback: an older version could resend a
    # retired request ID. Export/recovery needs an explicitly reviewed plan.
    raise RuntimeError("telegram_login_retention_forward_only_restore_reviewed_backup_before_rollback")
