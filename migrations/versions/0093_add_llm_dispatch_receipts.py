"""Define immutable preparation receipts; no activation or historical backfill.

Revision ID: 0093
Revises: 0092

Definition only until an owner approves schema application. Preparation is not
provider dispatch or admission. Evidence-preserving rollback retains this table;
ordinary downgrade is deliberately refused rather than deleting receipts.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0093"
down_revision = "0092"
branch_labels = None
depends_on = None
schema = settings.DB_SCHEMA


def upgrade() -> None:
    op.create_table(
        "llm_dispatch_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Integer(), sa.ForeignKey(f"{schema}.tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("dispatch_id", sa.String(32), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("utc_day", sa.Date(), nullable=False),
        sa.Column("llm_model_row_id", sa.Integer(), nullable=False),
        sa.Column("llm_provider_row_id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("provider_name", sa.String(200), nullable=False),
        sa.Column("input_cost_per_1k", sa.Numeric(18, 8), nullable=True),
        sa.Column("output_cost_per_1k", sa.Numeric(18, 8), nullable=True),
        sa.Column("tariff_state", sa.String(16), nullable=False),
        sa.Column("pricing_policy_version", sa.String(32), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("agent_task_id", sa.Integer(), nullable=True),
        sa.Column("agent_session_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("dispatch_id", name="uq_llm_dispatch_receipts_identity"),
        sa.CheckConstraint("dispatch_id ~ '^[0-9a-f]{32}$'", name="ck_llm_dispatch_receipts_identity"),
        sa.CheckConstraint(
            "tariff_state IN ('known', 'free', 'undefined')", name="ck_llm_dispatch_receipts_tariff_state"
        ),
        sa.CheckConstraint("utc_day = (prepared_at AT TIME ZONE 'UTC')::date", name="ck_llm_dispatch_receipts_utc_day"),
        sa.CheckConstraint(
            "(input_cost_per_1k IS NULL OR (input_cost_per_1k >= 0 AND input_cost_per_1k <= 9999999999.99999999)) "
            "AND (output_cost_per_1k IS NULL OR "
            "(output_cost_per_1k >= 0 AND output_cost_per_1k <= 9999999999.99999999))",
            name="ck_llm_dispatch_receipts_rates",
        ),
        sa.CheckConstraint(
            "(tariff_state = 'undefined' AND (input_cost_per_1k IS NULL OR output_cost_per_1k IS NULL)) OR "
            "(tariff_state = 'free' AND input_cost_per_1k IS NOT NULL AND output_cost_per_1k IS NOT NULL "
            "AND input_cost_per_1k = 0 AND output_cost_per_1k = 0) OR "
            "(tariff_state = 'known' AND input_cost_per_1k IS NOT NULL AND output_cost_per_1k IS NOT NULL "
            "AND input_cost_per_1k + output_cost_per_1k > 0)",
            name="ck_llm_dispatch_receipts_tariff_snapshot",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_llm_dispatch_receipts_tenant_day", "llm_dispatch_receipts", ["tenant_id", "utc_day"], schema=schema
    )
    qualified = op.get_bind().dialect.identifier_preparer.quote_schema(schema)
    op.execute(
        f"CREATE FUNCTION {qualified}.reject_llm_receipt_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN RAISE EXCEPTION 'Dispatch receipts are immutable' USING ERRCODE = '55000'; END; $$"
    )
    op.execute(
        f"CREATE TRIGGER llm_dispatch_receipts_immutable BEFORE UPDATE OR DELETE "
        f"ON {qualified}.llm_dispatch_receipts FOR EACH ROW "
        f"EXECUTE FUNCTION {qualified}.reject_llm_receipt_mutation()"
    )
    op.execute(
        f"CREATE TRIGGER llm_dispatch_receipts_no_truncate BEFORE TRUNCATE "
        f"ON {qualified}.llm_dispatch_receipts FOR EACH STATEMENT "
        f"EXECUTE FUNCTION {qualified}.reject_llm_receipt_mutation()"
    )


def downgrade() -> None:
    raise RuntimeError("Dispatch evidence is retained; downgrade requires a separately reviewed preservation plan")
