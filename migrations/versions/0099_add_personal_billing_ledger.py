"""Add reviewed personal billing boundaries, request bounds and ledger.

Revision ID: 0099
Revises: 0098
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0099"
down_revision = "0098"
branch_labels = None
depends_on = None
SCHEMA = settings.DB_SCHEMA


def _q(name: str) -> str:
    prep = op.get_bind().dialect.identifier_preparer
    return f"{prep.quote_schema(SCHEMA)}.{prep.quote(name)}"


def _immutable(table: str, stem: str, message: str) -> None:
    op.execute(
        f"CREATE FUNCTION {_q('reject_' + stem + '_mutation')}() RETURNS trigger LANGUAGE plpgsql AS $$ "
        f"BEGIN RAISE EXCEPTION '{message}' USING ERRCODE = '55000'; END; $$"
    )
    op.execute(
        f"CREATE TRIGGER {stem}_immutable BEFORE UPDATE OR DELETE ON {_q(table)} "
        f"FOR EACH ROW EXECUTE FUNCTION {_q('reject_' + stem + '_mutation')}()"
    )
    op.execute(
        f"CREATE TRIGGER {stem}_no_truncate BEFORE TRUNCATE ON {_q(table)} "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {_q('reject_' + stem + '_mutation')}()"
    )


def upgrade() -> None:
    op.create_table(
        "personal_billing_cutovers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.personal_billing_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "reviewed_by_user_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("review_version", sa.String(32), nullable=False, server_default="clean-midnight-v1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("account_id", name="uq_personal_billing_cutovers_account"),
        sa.CheckConstraint(
            "effective_at = date_trunc('day', effective_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'",
            name="ck_personal_billing_cutovers_utc_midnight",
        ),
        schema=SCHEMA,
    )
    _immutable("personal_billing_cutovers", "personal_billing_cutovers", "Billing cutovers are immutable")

    op.create_table(
        "personal_llm_bound_policies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "provider_row_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.llm_providers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "model_row_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.llm_models.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("provider_name", sa.String(200), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("max_input_tokens", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("request_mode", sa.String(20), nullable=False),
        sa.Column(
            "reviewed_by_user_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("provider_row_id", "model_row_id", "version", name="uq_personal_llm_bound_policy_version"),
        sa.CheckConstraint(
            "max_input_tokens > 0 AND max_output_tokens > 0", name="ck_personal_llm_bound_policy_tokens"
        ),
        sa.CheckConstraint("request_mode IN ('text_only', 'text_and_tools')", name="ck_personal_llm_bound_policy_mode"),
        schema=SCHEMA,
    )
    op.execute(
        f"CREATE FUNCTION {_q('check_personal_llm_bound_policy')}() RETURNS trigger LANGUAGE plpgsql AS $$ "
        f"DECLARE model_provider integer; model_snapshot varchar(200); provider_snapshot varchar(200); reviewer_active boolean; BEGIN "
        f"SELECT m.provider_id, m.model_id, p.name INTO model_provider, model_snapshot, provider_snapshot "
        f"FROM {_q('llm_models')} m JOIN {_q('llm_providers')} p ON p.id = m.provider_id WHERE m.id = NEW.model_row_id; "
        "IF model_provider IS DISTINCT FROM NEW.provider_row_id OR model_snapshot IS DISTINCT FROM NEW.model_id "
        "OR provider_snapshot IS DISTINCT FROM NEW.provider_name THEN "
        "RAISE EXCEPTION 'Bound policy provider/model mismatch' USING ERRCODE = '23514'; END IF; "
        f"SELECT u.is_active AND r.codename = 'admin' INTO reviewer_active FROM {_q('users')} u "
        f"JOIN {_q('roles')} r ON r.id = u.role_id WHERE u.id = NEW.reviewed_by_user_id; "
        "IF reviewer_active IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Bound policy reviewer must be active platform admin' USING ERRCODE = '23514'; END IF; "
        "RETURN NEW; END; $$"
    )
    op.execute(
        f"CREATE TRIGGER personal_llm_bound_policies_validate BEFORE INSERT ON {_q('personal_llm_bound_policies')} "
        f"FOR EACH ROW EXECUTE FUNCTION {_q('check_personal_llm_bound_policy')}()"
    )
    _immutable("personal_llm_bound_policies", "personal_llm_bound_policies", "Bound policies are immutable")

    op.create_table(
        "personal_llm_budget_days",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.personal_billing_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("utc_day", sa.Date(), nullable=False),
        sa.Column("settled_usd", sa.Numeric(18, 9), nullable=False, server_default="0"),
        sa.Column("reserved_usd", sa.Numeric(18, 9), nullable=False, server_default="0"),
        sa.Column("undefined_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unbounded_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unresolved_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("frozen", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("baseline_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("account_id", "utc_day", name="uq_personal_llm_budget_day_account_day"),
        sa.CheckConstraint(
            "settled_usd BETWEEN 0 AND 999999999.999999999 AND reserved_usd BETWEEN 0 AND 999999999.999999999",
            name="ck_personal_llm_budget_day_amounts",
        ),
        sa.CheckConstraint(
            "undefined_count >= 0 AND unbounded_count >= 0 AND unresolved_count >= 0",
            name="ck_personal_llm_budget_day_counts",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "personal_llm_reservations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "dispatch_id",
            sa.String(32),
            sa.ForeignKey(f"{SCHEMA}.personal_llm_dispatches.dispatch_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("utc_day", sa.Date(), nullable=False),
        sa.Column("ceiling_state", sa.String(16), nullable=False),
        sa.Column("ceiling_usd", sa.Numeric(18, 9), nullable=True),
        sa.Column("bound_version", sa.String(64), nullable=True),
        sa.Column("undefined_policy_version", sa.String(32), nullable=False),
        sa.Column("cap_usd", sa.Numeric(18, 9), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("dispatch_id", name="uq_personal_llm_reservations_dispatch"),
        sa.UniqueConstraint(
            "dispatch_id", "account_id", "utc_day", name="uq_personal_llm_reservations_dispatch_account_day"
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "utc_day"],
            [f"{SCHEMA}.personal_llm_budget_days.account_id", f"{SCHEMA}.personal_llm_budget_days.utc_day"],
            ondelete="RESTRICT",
            name="fk_personal_llm_reservations_day",
        ),
        sa.CheckConstraint(
            "ceiling_state IN ('bounded', 'unbounded', 'undefined')", name="ck_personal_llm_reservations_state"
        ),
        sa.CheckConstraint(
            "(ceiling_usd IS NULL OR ceiling_usd BETWEEN 0 AND 999999999.999999999) AND (cap_usd IS NULL OR cap_usd BETWEEN 0.000000001 AND 999999999.999999999)",
            name="ck_personal_llm_reservations_amounts",
        ),
        sa.CheckConstraint(
            "(ceiling_state = 'bounded' AND ceiling_usd IS NOT NULL AND (ceiling_usd = 0 OR bound_version IS NOT NULL)) OR (ceiling_state = 'unbounded' AND ceiling_usd IS NULL AND bound_version IS NULL AND cap_usd IS NULL) OR (ceiling_state = 'undefined' AND ceiling_usd = 0 AND bound_version IS NULL)",
            name="ck_personal_llm_reservations_bound",
        ),
        sa.CheckConstraint(
            "undefined_policy_version = 'owner-undefined-allow-v1'",
            name="ck_personal_llm_reservations_undefined_policy",
        ),
        schema=SCHEMA,
    )
    _immutable("personal_llm_reservations", "personal_llm_reservations", "Personal reservations are immutable")
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
    op.execute(
        f"CREATE TRIGGER personal_llm_reservations_validate BEFORE INSERT ON {_q('personal_llm_reservations')} "
        f"FOR EACH ROW EXECUTE FUNCTION {_q('check_personal_llm_reservation')}()"
    )

    op.create_table(
        "personal_llm_reservation_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "dispatch_id",
            sa.String(32),
            sa.ForeignKey(f"{SCHEMA}.personal_llm_reservations.dispatch_id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("utc_day", sa.Date(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reserved_usd", sa.Numeric(18, 9), nullable=False),
        sa.Column("settled_usd", sa.Numeric(18, 9), nullable=False),
        sa.Column("latest_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "latest_event_id",
            sa.String(32),
            sa.ForeignKey(f"{SCHEMA}.llm_outcome_events.event_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["dispatch_id", "account_id", "utc_day"],
            [
                f"{SCHEMA}.personal_llm_reservations.dispatch_id",
                f"{SCHEMA}.personal_llm_reservations.account_id",
                f"{SCHEMA}.personal_llm_reservations.utc_day",
            ],
            ondelete="RESTRICT",
            name="fk_personal_llm_reservation_states_reservation",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "utc_day"],
            [f"{SCHEMA}.personal_llm_budget_days.account_id", f"{SCHEMA}.personal_llm_budget_days.utc_day"],
            ondelete="RESTRICT",
            name="fk_personal_llm_reservation_states_day",
        ),
        sa.CheckConstraint(
            "settled_usd BETWEEN 0 AND 999999999.999999999 AND reserved_usd BETWEEN 0 AND 999999999.999999999",
            name="ck_personal_llm_reservation_states_amounts",
        ),
        sa.CheckConstraint(
            "status IN ('reserved', 'settled', 'released')", name="ck_personal_llm_reservation_states_status"
        ),
        schema=SCHEMA,
    )
    op.execute(
        f"CREATE FUNCTION {_q('check_personal_llm_reservation_state_event')}() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "DECLARE event_dispatch varchar(32); event_tenant integer; dispatch_tenant integer; BEGIN "
        "IF NEW.latest_event_id IS NULL THEN RETURN NEW; END IF; "
        f"SELECT dispatch_id, tenant_id INTO event_dispatch, event_tenant FROM {_q('llm_outcome_events')} "
        "WHERE event_id = NEW.latest_event_id; "
        f"SELECT tenant_id INTO dispatch_tenant FROM {_q('personal_llm_dispatches')} WHERE dispatch_id = NEW.dispatch_id; "
        "IF event_dispatch IS DISTINCT FROM NEW.dispatch_id OR event_tenant IS DISTINCT FROM dispatch_tenant THEN "
        "RAISE EXCEPTION 'Reservation state event differs from its dispatch workspace' USING ERRCODE = '23514'; END IF; "
        "RETURN NEW; END; $$"
    )
    op.execute(
        f"CREATE TRIGGER personal_llm_reservation_states_event_validate BEFORE INSERT OR UPDATE ON {_q('personal_llm_reservation_states')} "
        f"FOR EACH ROW EXECUTE FUNCTION {_q('check_personal_llm_reservation_state_event')}()"
    )
    op.create_table(
        "personal_llm_ledger_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entry_key", sa.String(32), nullable=False),
        sa.Column(
            "dispatch_id",
            sa.String(32),
            sa.ForeignKey(f"{SCHEMA}.personal_llm_reservations.dispatch_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("utc_day", sa.Date(), nullable=False),
        sa.Column(
            "event_id",
            sa.String(32),
            sa.ForeignKey(f"{SCHEMA}.llm_outcome_events.event_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("entry_kind", sa.String(16), nullable=False),
        sa.Column("reserved_delta_usd", sa.Numeric(18, 9), nullable=False),
        sa.Column("settled_delta_usd", sa.Numeric(18, 9), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("entry_key", name="uq_personal_llm_ledger_entry_key"),
        sa.UniqueConstraint("event_id", "entry_kind", name="uq_personal_llm_ledger_event_kind"),
        sa.ForeignKeyConstraint(
            ["account_id", "utc_day"],
            [f"{SCHEMA}.personal_llm_budget_days.account_id", f"{SCHEMA}.personal_llm_budget_days.utc_day"],
            ondelete="RESTRICT",
            name="fk_personal_llm_ledger_entries_day",
        ),
        sa.CheckConstraint("entry_key ~ '^[0-9a-f]{32}$'", name="ck_personal_llm_ledger_entry_key"),
        sa.CheckConstraint(
            "entry_kind IN ('reserve', 'observation', 'settlement', 'release')",
            name="ck_personal_llm_ledger_entry_kind",
        ),
        sa.CheckConstraint(
            "reserved_delta_usd BETWEEN -999999999.999999999 AND 999999999.999999999 AND settled_delta_usd BETWEEN -999999999.999999999 AND 999999999.999999999",
            name="ck_personal_llm_ledger_entry_deltas",
        ),
        schema=SCHEMA,
    )
    _immutable("personal_llm_ledger_entries", "personal_llm_ledger_entries", "Personal ledger entries are immutable")

    op.execute(
        f"CREATE FUNCTION {_q('check_personal_llm_ledger_entry')}() RETURNS trigger LANGUAGE plpgsql AS $$ "
        f"DECLARE valid boolean; event_dispatch varchar(32); event_tenant integer; dispatch_tenant integer; BEGIN "
        f"SELECT (r.account_id = NEW.account_id AND r.utc_day = NEW.utc_day) INTO valid "
        f"FROM {_q('personal_llm_reservations')} r WHERE r.dispatch_id = NEW.dispatch_id; "
        "IF valid IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Ledger entry differs from its reservation' USING ERRCODE = '23514'; END IF; "
        "IF NEW.event_id IS NOT NULL THEN "
        f"SELECT dispatch_id, tenant_id INTO event_dispatch, event_tenant FROM {_q('llm_outcome_events')} WHERE event_id = NEW.event_id; "
        f"SELECT tenant_id INTO dispatch_tenant FROM {_q('personal_llm_dispatches')} WHERE dispatch_id = NEW.dispatch_id; "
        "IF event_dispatch IS DISTINCT FROM NEW.dispatch_id OR event_tenant IS NULL OR dispatch_tenant IS DISTINCT FROM event_tenant THEN "
        "RAISE EXCEPTION 'Ledger outcome does not match its dispatch workspace' USING ERRCODE = '23514'; END IF; END IF; "
        "RETURN NEW; END; $$"
    )
    op.execute(
        f"CREATE TRIGGER personal_llm_ledger_entries_validate BEFORE INSERT ON {_q('personal_llm_ledger_entries')} "
        f"FOR EACH ROW EXECUTE FUNCTION {_q('check_personal_llm_ledger_entry')}()"
    )

    op.create_table(
        "personal_llm_billing_incidents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.String(32),
            sa.ForeignKey(f"{SCHEMA}.llm_outcome_events.event_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "dispatch_id",
            sa.String(32),
            sa.ForeignKey(f"{SCHEMA}.personal_llm_reservations.dispatch_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.personal_billing_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("utc_day", sa.Date(), nullable=False),
        sa.Column("incident_kind", sa.String(32), nullable=False),
        sa.Column("reserved_usd", sa.Numeric(18, 9), nullable=False),
        sa.Column("actual_usd", sa.Numeric(18, 9), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("event_id", name="uq_personal_llm_billing_incident_event"),
        sa.ForeignKeyConstraint(
            ["account_id", "utc_day"],
            [f"{SCHEMA}.personal_llm_budget_days.account_id", f"{SCHEMA}.personal_llm_budget_days.utc_day"],
            ondelete="RESTRICT",
            name="fk_personal_llm_billing_incident_day",
        ),
        sa.CheckConstraint(
            "incident_kind IN ('bound_breach', 'cap_overrun', 'bound_and_cap_overrun', 'terminal_conflict')",
            name="ck_personal_llm_billing_incident_kind",
        ),
        sa.CheckConstraint(
            "(actual_usd IS NULL OR (actual_usd BETWEEN 0 AND 999999999.999999999)) AND "
            "reserved_usd BETWEEN 0 AND 999999999.999999999",
            name="ck_personal_llm_billing_incident_amounts",
        ),
        schema=SCHEMA,
    )
    op.execute(
        f"CREATE FUNCTION {_q('check_personal_llm_billing_incident')}() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "DECLARE event_dispatch varchar(32); event_tenant integer; dispatch_tenant integer; reservation_account integer; reservation_day date; BEGIN "
        f"SELECT dispatch_id, tenant_id INTO event_dispatch, event_tenant FROM {_q('llm_outcome_events')} WHERE event_id = NEW.event_id; "
        f"SELECT account_id, utc_day INTO reservation_account, reservation_day FROM {_q('personal_llm_reservations')} WHERE dispatch_id = NEW.dispatch_id; "
        f"SELECT tenant_id INTO dispatch_tenant FROM {_q('personal_llm_dispatches')} WHERE dispatch_id = NEW.dispatch_id; "
        "IF event_dispatch IS DISTINCT FROM NEW.dispatch_id OR reservation_account IS DISTINCT FROM NEW.account_id "
        "OR reservation_day IS DISTINCT FROM NEW.utc_day OR event_tenant IS DISTINCT FROM dispatch_tenant THEN "
        "RAISE EXCEPTION 'Billing incident differs from its stored event/reservation' USING ERRCODE = '23514'; END IF; "
        "RETURN NEW; END; $$"
    )
    op.execute(
        f"CREATE TRIGGER personal_llm_billing_incidents_validate BEFORE INSERT ON {_q('personal_llm_billing_incidents')} "
        f"FOR EACH ROW EXECUTE FUNCTION {_q('check_personal_llm_billing_incident')}()"
    )
    _immutable("personal_llm_billing_incidents", "personal_llm_billing_incidents", "Billing incidents are immutable")

    op.execute(
        f"CREATE FUNCTION {_q('check_personal_billing_cutover')}() RETURNS trigger LANGUAGE plpgsql AS $$ "
        f"DECLARE ready boolean; reviewer boolean; BEGIN "
        f"SELECT is_active AND configured_at IS NOT NULL INTO ready FROM {_q('personal_billing_accounts')} WHERE id = NEW.account_id; "
        f"SELECT u.is_active AND r.codename = 'admin' INTO reviewer FROM {_q('users')} u "
        f"JOIN {_q('roles')} r ON r.id = u.role_id WHERE u.id = NEW.reviewed_by_user_id; "
        "IF ready IS DISTINCT FROM TRUE OR reviewer IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Cutover requires configured account and active platform reviewer' USING ERRCODE = '23514'; END IF; "
        "RETURN NEW; END; $$"
    )
    op.execute(
        f"CREATE TRIGGER personal_billing_cutovers_validate BEFORE INSERT ON {_q('personal_billing_cutovers')} "
        f"FOR EACH ROW EXECUTE FUNCTION {_q('check_personal_billing_cutover')}()"
    )


def downgrade() -> None:
    raise RuntimeError("Personal billing history is retained; downgrade requires a reviewed preservation plan")
