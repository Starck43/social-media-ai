"""Replace the reservation validator with an equivalent unambiguous PL/pgSQL.

Revision ID: 0102
Revises: 0101

The `check_personal_llm_reservation` function added by 0099 declares locals
`model_row_id`, `provider_name` and `model_id` that collide with the columns of
the `personal_llm_bound_policies p` subquery, so the bound-policy comparison can
resolve against the table column instead of the value read from the dispatch.
This revision drops and recreates the validation function with names that
cannot collide, keeping every check message, error code and trigger contract
identical. The immutability triggers and all data stay untouched.
"""

from __future__ import annotations

from alembic import op

from app.core.config import settings

revision = "0102"
down_revision = "0101"
branch_labels = None
depends_on = None
SCHEMA = settings.DB_SCHEMA


def _q(name: str) -> str:
    prep = op.get_bind().dialect.identifier_preparer
    return f"{prep.quote_schema(SCHEMA)}.{prep.quote(name)}"


def _validate_trigger() -> None:
    op.execute(
        f"CREATE TRIGGER personal_llm_reservations_validate BEFORE INSERT ON {_q('personal_llm_reservations')} "
        f"FOR EACH ROW EXECUTE FUNCTION {_q('check_personal_llm_reservation')}()"
    )


def upgrade() -> None:
    op.execute(
        f"DROP TRIGGER IF EXISTS personal_llm_reservations_validate ON {_q('personal_llm_reservations')}"
    )
    op.execute(f"DROP FUNCTION IF EXISTS {_q('check_personal_llm_reservation')}()")
    op.execute(
        f"CREATE FUNCTION {_q('check_personal_llm_reservation')}() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "DECLARE a_limit numeric; prepared timestamptz; dispatch_account integer; cutover timestamptz; "
        "provider_id integer; dispatch_model_row_id integer; dispatch_provider_name varchar(200); dispatch_model_id varchar(200); policy_found boolean; BEGIN "
        f"SELECT a.daily_limit_usd, d.prepared_at, d.account_id, c.effective_at, d.llm_provider_row_id, d.llm_model_row_id, d.provider_name, d.model_id "
        f"INTO a_limit, prepared, dispatch_account, cutover, provider_id, dispatch_model_row_id, dispatch_provider_name, dispatch_model_id "
        f"FROM {_q('personal_llm_dispatches')} d "
        f"JOIN {_q('personal_billing_accounts')} a ON a.id = d.account_id "
        f"LEFT JOIN {_q('personal_billing_cutovers')} c ON c.account_id = d.account_id "
        "WHERE d.dispatch_id = NEW.dispatch_id; "
        "IF dispatch_account IS DISTINCT FROM NEW.account_id OR prepared IS NULL OR "
        "NEW.utc_day IS DISTINCT FROM (prepared AT TIME ZONE 'UTC')::date OR cutover IS NULL OR prepared < cutover THEN "
        "RAISE EXCEPTION 'Personal reservation has no active reviewed cutover/dispatch binding' USING ERRCODE = '23514'; END IF; "
        "IF a_limit IS NULL OR (a_limit = 0 AND NEW.cap_usd IS NOT NULL) OR "
        "(a_limit > 0 AND NEW.cap_usd IS DISTINCT FROM a_limit) THEN "
        "RAISE EXCEPTION 'Reservation cap differs from configured account policy' USING ERRCODE = '23514'; END IF; "
        "IF NEW.ceiling_state = 'bounded' AND NEW.ceiling_usd > 0 THEN "
        f"SELECT EXISTS (SELECT 1 FROM {_q('personal_llm_bound_policies')} p WHERE "
        "p.provider_row_id = provider_id AND p.model_row_id = dispatch_model_row_id AND p.provider_name = dispatch_provider_name "
        "AND p.model_id = dispatch_model_id AND p.version = NEW.bound_version) INTO policy_found; "
        "IF policy_found IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Reservation bound is not a reviewed policy' USING ERRCODE = '23514'; END IF; END IF; "
        "RETURN NEW; END; $$"
    )
    _validate_trigger()


def downgrade() -> None:
    """Restore the exact 0099 validator so a rollback leaves a coherent state."""
    op.execute(
        f"DROP TRIGGER IF EXISTS personal_llm_reservations_validate ON {_q('personal_llm_reservations')}"
    )
    op.execute(f"DROP FUNCTION IF EXISTS {_q('check_personal_llm_reservation')}()")
    op.execute(
        f"CREATE FUNCTION {_q('check_personal_llm_reservation')}() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "DECLARE a_limit numeric; prepared timestamptz; dispatch_account integer; cutover timestamptz; "
        "provider_id integer; model_row_id integer; provider_name varchar(200); model_id varchar(200); policy_found boolean; BEGIN "
        f"SELECT a.daily_limit_usd, d.prepared_at, d.account_id, c.effective_at, d.llm_provider_row_id, d.llm_model_row_id, d.provider_name, d.model_id "
        f"INTO a_limit, prepared, dispatch_account, cutover, provider_id, model_row_id, provider_name, model_id "
        f"FROM {_q('personal_llm_dispatches')} d "
        f"JOIN {_q('personal_billing_accounts')} a ON a.id = d.account_id "
        f"LEFT JOIN {_q('personal_billing_cutovers')} c ON c.account_id = d.account_id "
        "WHERE d.dispatch_id = NEW.dispatch_id; "
        "IF dispatch_account IS DISTINCT FROM NEW.account_id OR prepared IS NULL OR "
        "NEW.utc_day IS DISTINCT FROM (prepared AT TIME ZONE 'UTC')::date OR cutover IS NULL OR prepared < cutover THEN "
        "RAISE EXCEPTION 'Personal reservation has no active reviewed cutover/dispatch binding' USING ERRCODE = '23514'; END IF; "
        "IF a_limit IS NULL OR (a_limit = 0 AND NEW.cap_usd IS NOT NULL) OR "
        "(a_limit > 0 AND NEW.cap_usd IS DISTINCT FROM a_limit) THEN "
        "RAISE EXCEPTION 'Reservation cap differs from configured account policy' USING ERRCODE = '23514'; END IF; "
        "IF NEW.ceiling_state = 'bounded' AND NEW.ceiling_usd > 0 THEN "
        f"SELECT EXISTS (SELECT 1 FROM {_q('personal_llm_bound_policies')} p WHERE "
        "p.provider_row_id = provider_id AND p.model_row_id = model_row_id AND p.provider_name = provider_name "
        "AND p.model_id = model_id AND p.version = NEW.bound_version) INTO policy_found; "
        "IF policy_found IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Reservation bound is not a reviewed policy' USING ERRCODE = '23514'; END IF; END IF; "
        "RETURN NEW; END; $$"
    )
    _validate_trigger()