"""Shared login-failure counters for every login surface.

One global table, one row per throttle key: the account identifier (casefolded
username/email) or the client address. Counting in the database is what makes
the limit survive a cleared cookie and hold across processes.

Definition only: this revision does not deploy. Applying it to a target needs
the same separate authorization as any other schema change.

Revision ID: 0111
Revises: 0110
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0111"
down_revision = "0110"
branch_labels = None
depends_on = None
SCHEMA = settings.DB_SCHEMA


def upgrade() -> None:
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("identifier", sa.String(length=255), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "last_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("scope", "identifier", name="uq_login_attempts_scope_identifier"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("login_attempts", schema=SCHEMA)
