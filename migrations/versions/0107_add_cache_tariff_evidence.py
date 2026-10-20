"""Add explicit model price units and cache tariff evidence without repricing.

Revision ID: 0107
Revises: 0106

Existing models retain unit 1000 and raw prices; new models default to unit
1000000. Historical receipt and attempt snapshots remain unchanged.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import settings

revision = "0107"
down_revision = "0106"
branch_labels = None
depends_on = None
schema = settings.DB_SCHEMA

RATE_COLUMNS = (
    "cache_read_cost_per_1m",
    "cache_write_5m_cost_per_1m",
    "cache_write_1h_cost_per_1m",
)
RATE_TABLES = ("llm_models", "llm_dispatch_receipts", "llm_attempts")


def upgrade() -> None:
    op.add_column(
        "llm_models",
        sa.Column("pricing_token_unit", sa.Integer(), nullable=False, server_default="1000"),
        schema=schema,
    )
    op.create_check_constraint(
        "ck_llm_models_pricing_token_unit",
        "llm_models",
        "pricing_token_unit IN (1000, 1000000)",
        schema=schema,
    )
    op.alter_column("llm_models", "pricing_token_unit", server_default="1000000", schema=schema)

    for table in RATE_TABLES:
        for column in RATE_COLUMNS:
            op.add_column(table, sa.Column(column, sa.Numeric(18, 8), nullable=True), schema=schema)
    for column in RATE_COLUMNS:
        op.add_column("personal_llm_dispatches", sa.Column(column, sa.Numeric(18, 8), nullable=True), schema=schema)
    op.add_column(
        "personal_llm_dispatches", sa.Column("pricing_token_unit", sa.Integer(), nullable=True), schema=schema
    )

    for table in ("llm_models", "llm_attempts"):
        rate_checks = " AND ".join(f"({column} IS NULL OR {column} >= 0)" for column in RATE_COLUMNS)
        op.create_check_constraint(f"ck_{table}_cache_rates", table, rate_checks, schema=schema)

    op.add_column("llm_dispatch_receipts", sa.Column("pricing_token_unit", sa.Integer(), nullable=True), schema=schema)
    op.add_column("llm_attempts", sa.Column("pricing_token_unit", sa.Integer(), nullable=True), schema=schema)
    op.add_column("llm_attempts", sa.Column("pricing_policy_version", sa.String(32), nullable=True), schema=schema)
    op.add_column(
        "llm_attempts", sa.Column("cache_usage", postgresql.JSONB(astext_type=sa.Text()), nullable=True), schema=schema
    )
    op.add_column(
        "llm_outcome_events",
        sa.Column("cache_usage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema=schema,
    )
    op.create_check_constraint(
        "ck_llm_attempts_pricing_token_unit",
        "llm_attempts",
        "COALESCE((((pricing_policy_version IS NULL OR pricing_policy_version = 'provider-price-v1') "
        "AND (pricing_token_unit IS NULL OR pricing_token_unit = 1000) AND cache_usage IS NULL "
        "AND cache_read_cost_per_1m IS NULL AND cache_write_5m_cost_per_1m IS NULL "
        "AND cache_write_1h_cost_per_1m IS NULL) OR "
        "(pricing_policy_version = 'cache-tariff-v2' AND "
        "(pricing_token_unit IN (1000, 1000000) OR (pricing_token_unit IS NULL "
        "AND input_cost_per_1k IS NULL AND output_cost_per_1k IS NULL "
        "AND cache_read_cost_per_1m IS NULL AND cache_write_5m_cost_per_1m IS NULL "
        "AND cache_write_1h_cost_per_1m IS NULL)))), FALSE)",
        schema=schema,
    )
    op.drop_constraint("ck_personal_llm_dispatch_rates", "personal_llm_dispatches", schema=schema, type_="check")
    op.drop_constraint(
        "ck_personal_llm_dispatch_snapshot", "personal_llm_dispatches", schema=schema, type_="check"
    )
    personal_rate_checks = " AND ".join(
        f"({column} IS NULL OR ({column} >= 0 AND {column} <= 9999999999.99999999))"
        for column in ("input_cost_per_1k", "output_cost_per_1k", *RATE_COLUMNS)
    )
    op.create_check_constraint(
        "ck_personal_llm_dispatch_rates", "personal_llm_dispatches", personal_rate_checks, schema=schema
    )
    personal_legacy_snapshot = (
        "(pricing_policy_version = 'provider-price-v1' AND (pricing_token_unit IS NULL OR pricing_token_unit = 1000) "
        "AND cache_read_cost_per_1m IS NULL AND cache_write_5m_cost_per_1m IS NULL "
        "AND cache_write_1h_cost_per_1m IS NULL AND ("
        "(tariff_state = 'undefined' AND (input_cost_per_1k IS NULL OR output_cost_per_1k IS NULL)) OR "
        "(tariff_state = 'free' AND input_cost_per_1k IS NOT NULL AND output_cost_per_1k IS NOT NULL "
        "AND input_cost_per_1k = 0 AND output_cost_per_1k = 0) OR "
        "(tariff_state = 'known' AND input_cost_per_1k IS NOT NULL AND output_cost_per_1k IS NOT NULL "
        "AND input_cost_per_1k + output_cost_per_1k > 0)))"
    )
    personal_cache_snapshot = (
        "(pricing_policy_version = 'cache-tariff-v2' AND pricing_token_unit IN (1000, 1000000) "
        "AND ((tariff_state = 'undefined' "
        "AND (input_cost_per_1k IS NULL OR output_cost_per_1k IS NULL)) OR "
        "(tariff_state = 'free' AND input_cost_per_1k IS NOT NULL AND output_cost_per_1k IS NOT NULL "
        "AND cache_read_cost_per_1m IS NOT NULL AND cache_write_5m_cost_per_1m IS NOT NULL "
        "AND cache_write_1h_cost_per_1m IS NOT NULL AND input_cost_per_1k = 0 AND output_cost_per_1k = 0 "
        "AND cache_read_cost_per_1m = 0 AND cache_write_5m_cost_per_1m = 0 AND cache_write_1h_cost_per_1m = 0) OR "
        "(tariff_state = 'known' AND input_cost_per_1k IS NOT NULL AND output_cost_per_1k IS NOT NULL "
        "AND (cache_read_cost_per_1m IS DISTINCT FROM 0 OR cache_write_5m_cost_per_1m IS DISTINCT FROM 0 "
        "OR cache_write_1h_cost_per_1m IS DISTINCT FROM 0 OR input_cost_per_1k > 0 OR output_cost_per_1k > 0))))"
    )
    op.create_check_constraint(
        "ck_personal_llm_dispatch_snapshot",
        "personal_llm_dispatches",
        f"COALESCE(({personal_legacy_snapshot} OR {personal_cache_snapshot}), FALSE)",
        schema=schema,
    )

    op.drop_constraint("ck_llm_dispatch_receipts_rates", "llm_dispatch_receipts", schema=schema, type_="check")
    op.drop_constraint(
        "ck_llm_dispatch_receipts_tariff_snapshot", "llm_dispatch_receipts", schema=schema, type_="check"
    )
    all_rates = ["input_cost_per_1k", "output_cost_per_1k", *RATE_COLUMNS]
    bound_checks = " AND ".join(
        f"({column} IS NULL OR ({column} >= 0 AND {column} <= 9999999999.99999999))" for column in all_rates
    )
    op.create_check_constraint("ck_llm_dispatch_receipts_rates", "llm_dispatch_receipts", bound_checks, schema=schema)
    legacy_snapshot = (
        "(pricing_policy_version = 'provider-price-v1' AND ((tariff_state = 'undefined' "
        "AND (input_cost_per_1k IS NULL OR output_cost_per_1k IS NULL)) OR "
        "(tariff_state = 'free' AND input_cost_per_1k IS NOT NULL AND output_cost_per_1k IS NOT NULL "
        "AND input_cost_per_1k = 0 AND output_cost_per_1k = 0) OR "
        "(tariff_state = 'known' AND input_cost_per_1k IS NOT NULL AND output_cost_per_1k IS NOT NULL "
        "AND input_cost_per_1k + output_cost_per_1k > 0)) AND cache_read_cost_per_1m IS NULL "
        "AND cache_write_5m_cost_per_1m IS NULL AND cache_write_1h_cost_per_1m IS NULL "
        "AND (pricing_token_unit IS NULL OR pricing_token_unit = 1000))"
    )
    cache_snapshot = (
        "(pricing_policy_version = 'cache-tariff-v2' AND pricing_token_unit IN (1000, 1000000) AND ("
        "(tariff_state = 'undefined' AND (input_cost_per_1k IS NULL OR output_cost_per_1k IS NULL)) OR "
        "(tariff_state = 'free' AND input_cost_per_1k IS NOT NULL AND output_cost_per_1k IS NOT NULL "
        "AND cache_read_cost_per_1m IS NOT NULL AND cache_write_5m_cost_per_1m IS NOT NULL "
        "AND cache_write_1h_cost_per_1m IS NOT NULL AND input_cost_per_1k = 0 AND output_cost_per_1k = 0 "
        "AND cache_read_cost_per_1m = 0 AND cache_write_5m_cost_per_1m = 0 AND cache_write_1h_cost_per_1m = 0) OR "
        "(tariff_state = 'known' AND input_cost_per_1k IS NOT NULL AND output_cost_per_1k IS NOT NULL "
        "AND (cache_read_cost_per_1m IS DISTINCT FROM 0 OR cache_write_5m_cost_per_1m IS DISTINCT FROM 0 "
        "OR cache_write_1h_cost_per_1m IS DISTINCT FROM 0 OR input_cost_per_1k > 0 OR output_cost_per_1k > 0))))"
    )
    op.create_check_constraint(
        "ck_llm_dispatch_receipts_tariff_snapshot",
        "llm_dispatch_receipts",
        f"COALESCE(({legacy_snapshot} OR {cache_snapshot}), FALSE)",
        schema=schema,
    )

    qualified = op.get_bind().dialect.identifier_preparer.quote_schema(schema)
    op.execute(
        f"CREATE OR REPLACE FUNCTION {qualified}.check_llm_outcome_receipt() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "DECLARE receipt RECORD; BEGIN "
        f"SELECT tenant_id, pricing_policy_version, prepared_at, input_cost_per_1k, output_cost_per_1k, "
        f"cache_read_cost_per_1m, cache_write_5m_cost_per_1m, cache_write_1h_cost_per_1m, pricing_token_unit "
        f"INTO receipt FROM {qualified}.llm_dispatch_receipts WHERE dispatch_id = NEW.dispatch_id; "
        "IF NOT FOUND OR receipt.tenant_id IS DISTINCT FROM NEW.tenant_id "
        "OR receipt.pricing_policy_version IS DISTINCT FROM NEW.pricing_policy_version "
        "OR NEW.observed_at < receipt.prepared_at THEN "
        "RAISE EXCEPTION 'Outcome receipt binding differs' USING ERRCODE = '23503'; END IF; "
        "IF NEW.price_source = 'receipt_tariff' AND NEW.cost_certainty = 'free' AND ("
        "(receipt.pricing_policy_version = 'provider-price-v1' AND "
        "(receipt.input_cost_per_1k IS DISTINCT FROM 0 OR receipt.output_cost_per_1k IS DISTINCT FROM 0)) OR "
        "(receipt.pricing_policy_version = 'cache-tariff-v2' AND ("
        "receipt.input_cost_per_1k IS DISTINCT FROM 0 OR receipt.output_cost_per_1k IS DISTINCT FROM 0 OR "
        "receipt.cache_read_cost_per_1m IS DISTINCT FROM 0 OR receipt.cache_write_5m_cost_per_1m IS DISTINCT FROM 0 OR "
        "receipt.cache_write_1h_cost_per_1m IS DISTINCT FROM 0))) THEN "
        "RAISE EXCEPTION 'Outcome free tariff differs' USING ERRCODE = '23514'; END IF; "
        "IF NEW.provider_price_state = 'resolved' AND "
        "(NEW.provider_cost_raw::numeric IS DISTINCT FROM NEW.cost_usd "
        "OR (NEW.cost_certainty = 'free') IS DISTINCT FROM (NEW.cost_usd = 0)) THEN "
        "RAISE EXCEPTION 'Outcome provider amount differs' USING ERRCODE = '23514'; END IF; "
        "RETURN NEW; END; $$"
    )


def downgrade() -> None:
    raise RuntimeError("Cache tariff evidence is retained; downgrade requires a separately reviewed preservation plan")
