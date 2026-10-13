"""Define insert-only receipt-bound outcome evidence; no runtime activation.

Revision ID: 0094
Revises: 0093

Application requires separate owner approval. Ordinary downgrade is refused
to retain evidence; no previous migration or receipt is rewritten.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0094"
down_revision = "0093"
branch_labels = None
depends_on = None
schema = settings.DB_SCHEMA


def upgrade() -> None:
    op.create_table(
        "llm_outcome_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tenant_id", sa.Integer, sa.ForeignKey(f"{schema}.tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("event_id", sa.String(32), nullable=False),
        sa.Column(
            "dispatch_id",
            sa.String(32),
            sa.ForeignKey(f"{schema}.llm_dispatch_receipts.dispatch_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_kind", sa.String(32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatch_state", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("prompt_tokens", sa.Integer, nullable=True),
        sa.Column("completion_tokens", sa.Integer, nullable=True),
        sa.Column("provider_price_state", sa.String(16), nullable=False),
        sa.Column("provider_cost_raw", sa.String(128), nullable=True),
        sa.Column("cost_usd", sa.Numeric(18, 9), nullable=True),
        sa.Column("cost_certainty", sa.String(32), nullable=False),
        sa.Column("price_source", sa.String(32), nullable=False),
        sa.Column("pricing_policy_version", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("event_id", name="uq_llm_outcome_events_identity"),
        sa.CheckConstraint(
            "event_id ~ '^[0-9a-f]{32}$' AND dispatch_id ~ '^[0-9a-f]{32}$'", name="ck_llm_outcome_events_identity"
        ),
        sa.CheckConstraint("event_kind IN ('observation', 'reconciliation')", name="ck_llm_outcome_events_kind"),
        sa.CheckConstraint(
            "provider_price_state IN ('absent', 'resolved', 'unresolved') AND cost_certainty IN ('priced', 'free', 'undefined', 'not_dispatched') AND price_source IN ('provider', 'receipt_tariff', 'none', 'not_dispatched')",
            name="ck_llm_outcome_events_states",
        ),
        sa.CheckConstraint(
            "(dispatch_state = 'not_dispatched' AND outcome = 'not_dispatched') OR (dispatch_state = 'possible' AND outcome IN ('failed_transport', 'timeout', 'cancelled', 'unknown')) OR (dispatch_state = 'response_received' AND outcome IN ('succeeded', 'failed_output', 'failed_transport', 'timeout', 'cancelled', 'unknown'))",
            name="ck_llm_outcome_events_outcome",
        ),
        sa.CheckConstraint(
            "(prompt_tokens IS NULL OR prompt_tokens >= 0) AND (completion_tokens IS NULL OR completion_tokens >= 0)",
            name="ck_llm_outcome_events_usage",
        ),
        sa.CheckConstraint(
            "cost_usd IS NULL OR (cost_usd >= 0 AND cost_usd <= 999999999.999999999)",
            name="ck_llm_outcome_events_amount",
        ),
        sa.CheckConstraint(
            "(cost_certainty IN ('undefined', 'not_dispatched') AND cost_usd IS NULL) OR (cost_certainty = 'free' AND cost_usd IS NOT NULL AND cost_usd = 0) OR (cost_certainty = 'priced' AND cost_usd IS NOT NULL)",
            name="ck_llm_outcome_events_certainty",
        ),
        sa.CheckConstraint(
            "(provider_price_state = 'absent' AND provider_cost_raw IS NULL AND price_source IN ('receipt_tariff', 'none', 'not_dispatched')) OR (provider_price_state = 'resolved' AND provider_cost_raw IS NOT NULL AND price_source = 'provider' AND cost_certainty IN ('priced', 'free')) OR (provider_price_state = 'unresolved' AND price_source = 'provider' AND cost_certainty = 'undefined')",
            name="ck_llm_outcome_events_report",
        ),
        sa.CheckConstraint(
            "(dispatch_state = 'not_dispatched' AND cost_certainty = 'not_dispatched' AND price_source = 'not_dispatched' AND provider_price_state = 'absent' AND prompt_tokens IS NULL AND completion_tokens IS NULL) OR (dispatch_state <> 'not_dispatched' AND cost_certainty <> 'not_dispatched' AND price_source <> 'not_dispatched')",
            name="ck_llm_outcome_events_no_dispatch",
        ),
        sa.CheckConstraint(
            "provider_cost_raw IS NULL OR provider_cost_raw ~ '^[0-9]+([.][0-9]+)?([Ee][+-]?[0-9]+)?$'",
            name="ck_llm_outcome_events_raw_decimal",
        ),
        sa.CheckConstraint(
            "price_source <> 'none' OR (cost_usd IS NULL AND cost_certainty = 'undefined')",
            name="ck_llm_outcome_events_no_price",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_llm_outcome_events_tenant_receipt",
        "llm_outcome_events",
        ["tenant_id", "dispatch_id", "observed_at"],
        schema=schema,
    )
    qualified = op.get_bind().dialect.identifier_preparer.quote_schema(schema)
    op.execute(
        f"CREATE FUNCTION {qualified}.reject_llm_outcome_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN RAISE EXCEPTION 'Outcome evidence is immutable' USING ERRCODE = '55000'; END; $$"
    )
    op.execute(
        f"CREATE TRIGGER llm_outcome_events_immutable BEFORE UPDATE OR DELETE "
        f"ON {qualified}.llm_outcome_events FOR EACH ROW "
        f"EXECUTE FUNCTION {qualified}.reject_llm_outcome_mutation()"
    )
    op.execute(
        f"CREATE TRIGGER llm_outcome_events_no_truncate BEFORE TRUNCATE "
        f"ON {qualified}.llm_outcome_events FOR EACH STATEMENT "
        f"EXECUTE FUNCTION {qualified}.reject_llm_outcome_mutation()"
    )
    op.execute(
        f"CREATE FUNCTION {qualified}.check_llm_outcome_receipt() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "DECLARE receipt RECORD; BEGIN "
        f"SELECT tenant_id, pricing_policy_version, prepared_at, input_cost_per_1k, output_cost_per_1k "
        f"INTO receipt FROM {qualified}.llm_dispatch_receipts WHERE dispatch_id = NEW.dispatch_id; "
        "IF NOT FOUND OR receipt.tenant_id IS DISTINCT FROM NEW.tenant_id "
        "OR receipt.pricing_policy_version IS DISTINCT FROM NEW.pricing_policy_version "
        "OR NEW.observed_at < receipt.prepared_at THEN "
        "RAISE EXCEPTION 'Outcome receipt binding differs' USING ERRCODE = '23503'; END IF; "
        "IF NEW.price_source = 'receipt_tariff' AND NEW.cost_certainty = 'free' "
        "AND (receipt.input_cost_per_1k IS DISTINCT FROM 0 OR receipt.output_cost_per_1k IS DISTINCT FROM 0) THEN "
        "RAISE EXCEPTION 'Outcome free tariff differs' USING ERRCODE = '23514'; END IF; "
        "IF NEW.provider_price_state = 'resolved' AND "
        "(NEW.provider_cost_raw::numeric IS DISTINCT FROM NEW.cost_usd "
        "OR (NEW.cost_certainty = 'free') IS DISTINCT FROM (NEW.cost_usd = 0)) THEN "
        "RAISE EXCEPTION 'Outcome provider amount differs' USING ERRCODE = '23514'; END IF; "
        "RETURN NEW; END; $$"
    )
    op.execute(
        f"CREATE TRIGGER llm_outcome_events_receipt_binding BEFORE INSERT "
        f"ON {qualified}.llm_outcome_events FOR EACH ROW "
        f"EXECUTE FUNCTION {qualified}.check_llm_outcome_receipt()"
    )


def downgrade() -> None:
    raise RuntimeError("Outcome evidence is retained; downgrade requires a separately reviewed preservation plan")
