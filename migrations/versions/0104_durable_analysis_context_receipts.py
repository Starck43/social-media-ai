"""Persist successful analysis coverage by semantic context.

Existing analytics rows receive the unattributed ``legacy`` fingerprint only.
No content receipts are backfilled: older per-row hashes may be unions from
multiple runs and cannot safely be assigned to a scenario or target context.

Revision ID: 0104
Revises: 0103
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.core.config import settings

revision = "0104"
down_revision = "0103"
branch_labels = None
depends_on = None
SCHEMA = settings.DB_SCHEMA


def _q(name: str) -> str:
    prep = op.get_bind().dialect.identifier_preparer
    return f"{prep.quote_schema(SCHEMA)}.{prep.quote(name)}"


def upgrade() -> None:
    op.add_column(
        "ai_analytics",
        sa.Column("analysis_fingerprint", sa.String(length=64), nullable=False, server_default="legacy"),
        schema=SCHEMA,
    )
    op.drop_constraint("uq_analytics_source_date_period", "ai_analytics", schema=SCHEMA, type_="unique")
    op.create_unique_constraint(
        "uq_analytics_source_date_period_context",
        "ai_analytics",
        ["source_id", "analysis_date", "period_type", "analysis_fingerprint"],
        schema=SCHEMA,
    )
    op.create_table(
        "analyzed_content_receipts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("analysis_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("scenario_id", sa.Integer(), nullable=True),
        sa.Column("analytics_id", sa.Integer(), nullable=True),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], [f"{SCHEMA}.tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], [f"{SCHEMA}.sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scenario_id"], [f"{SCHEMA}.agent_scenarios.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["analytics_id"], [f"{SCHEMA}.ai_analytics.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "source_id", "content_hash", "analysis_fingerprint",
            name="uq_analyzed_content_receipt_identity",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_analyzed_content_receipts_source", "analyzed_content_receipts", ["tenant_id", "source_id"], schema=SCHEMA
    )


def downgrade() -> None:
    # The wider unique key may now contain multiple contexts for one daily
    # period. Refuse downgrade before any mutation rather than dropping one.
    ambiguous = op.get_bind().execute(sa.text(
        f"SELECT 1 FROM {_q('ai_analytics')} "
        "WHERE analysis_date IS NOT NULL GROUP BY source_id, analysis_date, period_type "
        "HAVING count(DISTINCT analysis_fingerprint) > 1 LIMIT 1"
    )).first()
    if ambiguous:
        raise RuntimeError("Cannot downgrade 0104: analytics rows contain multiple analysis contexts")
    op.drop_index("ix_analyzed_content_receipts_source", table_name="analyzed_content_receipts", schema=SCHEMA)
    op.drop_table("analyzed_content_receipts", schema=SCHEMA)
    op.drop_constraint("uq_analytics_source_date_period_context", "ai_analytics", schema=SCHEMA, type_="unique")
    op.drop_column("ai_analytics", "analysis_fingerprint", schema=SCHEMA)
    op.create_unique_constraint(
        "uq_analytics_source_date_period", "ai_analytics", ["source_id", "analysis_date", "period_type"],
        schema=SCHEMA,
    )
