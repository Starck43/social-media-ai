"""Define immutable reviewed baseline provenance; no application/backfill.

Revision ID: 0096
Revises: 0095
Existing balances/reservations and their migration definitions stay unchanged.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0096"
down_revision = "0095"
branch_labels = None
depends_on = None
schema = settings.DB_SCHEMA


def upgrade() -> None:
    op.create_table(
        "llm_budget_baselines",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tenant_id", sa.Integer, nullable=False),
        sa.Column("utc_day", sa.Date, nullable=False),
        sa.Column("settled_usd", sa.Numeric(18, 9), nullable=False),
        sa.Column("reserved_usd", sa.Numeric(18, 9), nullable=False),
        sa.Column("undefined_count", sa.Integer, nullable=False),
        sa.Column("unbounded_count", sa.Integer, nullable=False),
        sa.Column("frozen", sa.Boolean, nullable=False),
        sa.Column("baseline_version", sa.String(64), nullable=False),
        sa.Column("reviewed_by", sa.String(64), nullable=False),
        sa.Column("review_ref", sa.String(64), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fence_ref", sa.String(64), nullable=False),
        sa.Column("fence_version", sa.String(32), nullable=False),
        sa.UniqueConstraint("tenant_id", "utc_day", name="uq_llm_budget_baselines_payer_day"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "utc_day"],
            [f"{schema}.llm_budget_days.tenant_id", f"{schema}.llm_budget_days.utc_day"],
            ondelete="RESTRICT",
            name="fk_llm_budget_baselines_day",
        ),
        sa.CheckConstraint(
            "settled_usd >= 0 AND settled_usd <= 999999999.999999999 AND reserved_usd >= 0 AND reserved_usd "
            "<= 999999999.999999999",
            name="ck_llm_budget_baselines_amounts",
        ),
        sa.CheckConstraint("undefined_count >= 0 AND unbounded_count >= 0", name="ck_llm_budget_baselines_counts"),
        sa.CheckConstraint("frozen", name="ck_llm_budget_baselines_frozen"),
        sa.CheckConstraint(
            "baseline_version ~ '^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}$' AND "
            "reviewed_by ~ '^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}$' AND "
            "review_ref ~ '^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}$' AND "
            "fence_ref ~ '^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}$' AND "
            "evidence_sha256 ~ '^[0-9a-f]{64}$' AND fence_version = 'quiesced-accounted-v1'",
            name="ck_llm_budget_baselines_review",
        ),
        sa.CheckConstraint(
            "utc_day >= DATE '0001-01-01' AND utc_day < DATE '9999-12-31' AND "
            "cutoff_at >= (utc_day::timestamp AT TIME ZONE 'UTC') AND "
            "cutoff_at <= ((utc_day + 1)::timestamp AT TIME ZONE 'UTC') AND reviewed_at >= cutoff_at",
            name="ck_llm_budget_baselines_times",
        ),
        schema=schema,
    )
    qualified = op.get_bind().dialect.identifier_preparer.quote_schema(schema)
    op.execute(
        f"CREATE FUNCTION {qualified}.reject_llm_budget_baseline_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN RAISE EXCEPTION 'Reviewed baseline is immutable' USING ERRCODE = '55000'; END; $$"
    )
    op.execute(
        f"CREATE TRIGGER llm_budget_baselines_immutable BEFORE UPDATE OR DELETE "
        f"ON {qualified}.llm_budget_baselines FOR EACH ROW "
        f"EXECUTE FUNCTION {qualified}.reject_llm_budget_baseline_mutation()"
    )
    op.execute(
        f"CREATE TRIGGER llm_budget_baselines_no_truncate BEFORE TRUNCATE "
        f"ON {qualified}.llm_budget_baselines FOR EACH STATEMENT "
        f"EXECUTE FUNCTION {qualified}.reject_llm_budget_baseline_mutation()"
    )
    op.execute(
        f"CREATE FUNCTION {qualified}.check_llm_budget_baseline_day() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "DECLARE day_row RECORD; BEGIN "
        f"SELECT * INTO day_row FROM {qualified}.llm_budget_days "
        "WHERE tenant_id = NEW.tenant_id AND utc_day = NEW.utc_day FOR UPDATE; "
        "IF NOT FOUND OR day_row.settled_usd IS DISTINCT FROM NEW.settled_usd "
        "OR day_row.reserved_usd IS DISTINCT FROM NEW.reserved_usd "
        "OR day_row.undefined_count IS DISTINCT FROM NEW.undefined_count "
        "OR day_row.unbounded_count IS DISTINCT FROM NEW.unbounded_count "
        "OR day_row.frozen IS DISTINCT FROM NEW.frozen "
        "OR day_row.baseline_version IS DISTINCT FROM NEW.baseline_version THEN "
        "RAISE EXCEPTION 'Reviewed baseline day binding differs' USING ERRCODE = '23514'; END IF; "
        "RETURN NEW; END; $$"
    )
    op.execute(
        f"CREATE TRIGGER llm_budget_baselines_day_binding BEFORE INSERT "
        f"ON {qualified}.llm_budget_baselines FOR EACH ROW "
        f"EXECUTE FUNCTION {qualified}.check_llm_budget_baseline_day()"
    )


def downgrade() -> None:
    raise RuntimeError("Reviewed baselines are retained; downgrade requires a reviewed preservation plan")
