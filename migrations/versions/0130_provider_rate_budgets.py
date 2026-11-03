"""Technical provider-account token buckets; provisional revision.

Revision ID: p13t03_rate_budget
Revises: 0129
Rebase/renumber in the coordinated migration queue before merge.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "p13t03_rate_budget"
down_revision = "0129"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "provider_rate_budgets",
        sa.Column("provider", sa.String(30), primary_key=True),
        sa.Column("account_key", sa.String(64), primary_key=True),
        sa.Column("tokens", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("tokens >= 0", name="ck_provider_rate_tokens_nonnegative"),
        sa.CheckConstraint("length(account_key) = 64", name="ck_provider_rate_account_digest"),
        schema=settings.DB_SCHEMA,
    )


def downgrade():
    op.drop_table("provider_rate_budgets", schema=settings.DB_SCHEMA)
