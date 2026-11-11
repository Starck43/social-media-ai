"""P20-T12 durable per-tenant web research quota state.

Revision ID: p20t12_web_research_quotas
Revises: 0151

Stores atomic per-(tenant_id, account_key) daily quota and burst rate-limit
state for web research. Downgrade refuses when persisted quota state is non-empty.
"""

import sqlalchemy as sa
from alembic import context, op

from app.core.config import settings

revision = "p20t12_web_research_quotas"
down_revision = "0151"
branch_labels = None
depends_on = None


def upgrade():
    s = settings.DB_SCHEMA
    op.create_table(
        "web_research_quotas",
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("account_key", sa.String(80), primary_key=True),
        sa.Column("day_key", sa.String(10), nullable=False),
        sa.Column("daily_used", sa.Integer(), nullable=False),
        sa.Column("daily_quota", sa.Integer(), nullable=False),
        sa.Column("burst_tokens", sa.Float(), nullable=False),
        sa.Column("burst_limit", sa.Integer(), nullable=False),
        sa.Column("rate_per_minute", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "daily_used >= 0 AND daily_quota > 0 AND daily_used <= daily_quota",
            name="ck_web_research_quotas_daily",
        ),
        sa.CheckConstraint(
            "burst_tokens >= 0 AND burst_limit > 0 AND rate_per_minute > 0",
            name="ck_web_research_quotas_burst",
        ),
        sa.CheckConstraint(
            "length(account_key) >= 1 AND length(account_key) <= 80",
            name="ck_web_research_quotas_account_key",
        ),
        sa.CheckConstraint(
            "length(day_key) = 10",
            name="ck_web_research_quotas_day_key",
        ),
        schema=s,
    )


def downgrade():
    if context.is_offline_mode():
        raise RuntimeError("Cannot safely downgrade web research quotas offline")
    s = settings.DB_SCHEMA
    connection = op.get_bind()
    quoted_schema = connection.dialect.identifier_preparer.quote_schema(s)
    connection.execute(sa.text(f"LOCK TABLE {quoted_schema}.web_research_quotas IN ACCESS EXCLUSIVE MODE"))
    has_rows = connection.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {quoted_schema}.web_research_quotas)"))
    if has_rows:
        raise RuntimeError("Downgrade refused: web_research_quotas contains persisted tenant quota state")
    op.drop_table("web_research_quotas", schema=s)
