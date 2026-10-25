"""Persist personal notification presentation preferences.

Revision ID: 0098
Revises: 0097
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0098"
down_revision = "0097"
branch_labels = None
depends_on = None

SCHEMA = settings.DB_SCHEMA


def upgrade() -> None:
    op.create_table(
        "notification_preferences",
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("connections", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("tasks", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("popup", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("quiet", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("start", sa.String(5), nullable=False, server_default="22:00"),
        sa.Column("end", sa.String(5), nullable=False, server_default="08:00"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("version = 1", name="ck_notification_preferences_version"),
        sa.CheckConstraint("\"start\" ~ '^(?:[01][0-9]|2[0-3]):[0-5][0-9]$'", name="ck_notification_preferences_start"),
        sa.CheckConstraint("\"end\" ~ '^(?:[01][0-9]|2[0-3]):[0-5][0-9]$'", name="ck_notification_preferences_end"),
        sa.CheckConstraint('NOT quiet OR "start" <> "end"', name="ck_notification_preferences_quiet_interval"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("notification_preferences", schema=SCHEMA)
