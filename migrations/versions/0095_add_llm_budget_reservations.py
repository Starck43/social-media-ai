"""Define unwired balances/reservations; application needs a separate PG plan.

Revision ID: 0095
Revises: 0094
No baseline rows, previous migration edits, runtime creation or cutover.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0095"
down_revision = "0094"
branch_labels = None
depends_on = None
schema = settings.DB_SCHEMA


def upgrade() -> None:
    op.create_table(
        "llm_budget_days",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tenant_id", sa.Integer, sa.ForeignKey(f"{schema}.tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("utc_day", sa.Date, nullable=False),
        sa.Column("settled_usd", sa.Numeric(18, 9), nullable=False),
        sa.Column("reserved_usd", sa.Numeric(18, 9), nullable=False),
        sa.Column("undefined_count", sa.Integer, nullable=False),
        sa.Column("unbounded_count", sa.Integer, nullable=False),
        sa.Column("frozen", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("baseline_version", sa.String(64), nullable=False),
        sa.UniqueConstraint("tenant_id", "utc_day", name="uq_llm_budget_days_payer_day"),
        sa.CheckConstraint(
            "settled_usd >= 0 AND settled_usd <= 999999999.999999999 AND reserved_usd >= 0 AND reserved_usd "
            "<= 999999999.999999999",
            name="ck_llm_budget_days_amounts",
        ),
        sa.CheckConstraint("undefined_count >= 0 AND unbounded_count >= 0", name="ck_llm_budget_days_counts"),
        schema=schema,
    )
    op.create_table(
        "llm_budget_reservations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tenant_id", sa.Integer, sa.ForeignKey(f"{schema}.tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("utc_day", sa.Date, nullable=False),
        sa.Column(
            "dispatch_id", sa.String(32),
            sa.ForeignKey(f"{schema}.llm_dispatch_receipts.dispatch_id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("ceiling_state", sa.String(16), nullable=False),
        sa.Column("ceiling_usd", sa.Numeric(18, 9), nullable=True),
        sa.Column("bound_version", sa.String(64), nullable=True),
        sa.Column("undefined_policy_version", sa.String(32), nullable=False),
        sa.Column("cap_usd", sa.Numeric(18, 9), nullable=True),
        sa.UniqueConstraint("dispatch_id", name="uq_llm_budget_reservations_identity"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "utc_day"],
            [f"{schema}.llm_budget_days.tenant_id", f"{schema}.llm_budget_days.utc_day"],
            ondelete="RESTRICT", name="fk_llm_budget_reservations_day",
        ),
        sa.CheckConstraint("dispatch_id ~ '^[0-9a-f]{32}$'", name="ck_llm_budget_reservations_identity"),
        sa.CheckConstraint(
            "ceiling_state IN ('bounded', 'unbounded', 'undefined')",
            name="ck_llm_budget_reservations_state",
        ),
        sa.CheckConstraint(
            "(ceiling_usd IS NULL OR (ceiling_usd >= 0 AND ceiling_usd <= 999999999.999999999)) AND (cap_usd "
            "IS NULL OR (cap_usd > 0 AND cap_usd <= 999999999.999999999))",
            name="ck_llm_budget_reservations_amounts",
        ),
        sa.CheckConstraint(
            "(ceiling_state = 'bounded' AND ceiling_usd IS NOT NULL AND (ceiling_usd = 0 OR bound_version IS "
            "NOT NULL)) OR (ceiling_state = 'unbounded' AND ceiling_usd IS NULL AND bound_version IS NULL AND "
            "cap_usd IS NULL) OR (ceiling_state = 'undefined' AND ceiling_usd IS NOT NULL AND ceiling_usd = 0 "
            "AND bound_version IS NULL)",
            name="ck_llm_budget_reservations_bound",
        ),
        sa.CheckConstraint(
            "undefined_policy_version = 'owner-undefined-allow-v1'",
            name="ck_llm_budget_reservations_policy",
        ),
        schema=schema,
    )
    qualified = op.get_bind().dialect.identifier_preparer.quote_schema(schema)
    op.execute(
        f"CREATE FUNCTION {qualified}.reject_llm_budget_reservation_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN RAISE EXCEPTION 'Budget reservation is immutable' USING ERRCODE = '55000'; END; $$"
    )
    op.execute(
        f"CREATE TRIGGER llm_budget_reservations_immutable BEFORE UPDATE OR DELETE "
        f"ON {qualified}.llm_budget_reservations FOR EACH ROW "
        f"EXECUTE FUNCTION {qualified}.reject_llm_budget_reservation_mutation()"
    )
    op.execute(
        f"CREATE TRIGGER llm_budget_reservations_no_truncate BEFORE TRUNCATE "
        f"ON {qualified}.llm_budget_reservations FOR EACH STATEMENT "
        f"EXECUTE FUNCTION {qualified}.reject_llm_budget_reservation_mutation()"
    )
    op.execute(
        f"CREATE FUNCTION {qualified}.check_llm_budget_reservation_receipt() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "DECLARE receipt RECORD; BEGIN "
        f"SELECT tenant_id, utc_day, tariff_state INTO receipt FROM {qualified}.llm_dispatch_receipts "
        "WHERE dispatch_id = NEW.dispatch_id; "
        "IF NOT FOUND OR receipt.tenant_id IS DISTINCT FROM NEW.tenant_id "
        "OR receipt.utc_day IS DISTINCT FROM NEW.utc_day THEN "
        "RAISE EXCEPTION 'Reservation receipt binding differs' USING ERRCODE = '23503'; END IF; "
        "IF (receipt.tariff_state = 'undefined' AND NEW.ceiling_state <> 'undefined') "
        "OR (receipt.tariff_state = 'free' AND (NEW.ceiling_state <> 'bounded' "
        "OR NEW.ceiling_usd IS DISTINCT FROM 0 OR NEW.bound_version IS NOT NULL)) "
        "OR (receipt.tariff_state = 'known' AND (NEW.ceiling_state = 'undefined' "
        "OR (NEW.ceiling_state = 'bounded' AND (NEW.ceiling_usd IS NULL OR NEW.ceiling_usd <= 0)))) THEN "
        "RAISE EXCEPTION 'Reservation tariff basis differs' USING ERRCODE = '23514'; END IF; "
        "RETURN NEW; END; $$"
    )
    op.execute(
        f"CREATE TRIGGER llm_budget_reservations_receipt_binding BEFORE INSERT "
        f"ON {qualified}.llm_budget_reservations FOR EACH ROW "
        f"EXECUTE FUNCTION {qualified}.check_llm_budget_reservation_receipt()"
    )


def downgrade() -> None:
    raise RuntimeError("Reservations are retained; downgrade requires a reviewed preservation plan")
