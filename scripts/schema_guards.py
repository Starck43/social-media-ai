#!/usr/bin/env python3
"""Reviewed source of the plpgsql guard objects carried only by migration history.

`Base.metadata.create_all` creates tables, columns and constraints, but no
trigger function or trigger: the immutable-receipt, personal-billing and
task-binding guards live exclusively in migrations 0093-0109. A schema built
from the metadata therefore accepts rows the migrated schema refuses — the
personal billing acceptance suite failed seven cases against such a schema
until it was repaired by hand. This module keeps the final, post-migration
state of those objects so the test-schema build path can reproduce them.

Rules this module follows:

* One entry per behaviour-stable object. Later migrations that only replace a
  function body (0102, 0103, 0107) are already folded into the single entry,
  not stacked on top of the earlier text.
* Function bodies carry no schema literal: ``{s}`` is substituted with the
  target schema, so a trigger never executes a function of another schema.
* Trigger definitions follow the migrations' shape; ``CREATE TRIGGER`` has no
  ``IF NOT EXISTS``, so applying drops the trigger first (see
  :func:`apply_schema_guards`).
* The entries are deliberately not imported from the migration modules: those
  import Alembic and application settings at module scope, and their bodies
  build f-strings for one schema at import time.

`tests/tooling/test_p11_t24_schema_guards.py` re-reads the migrations with AST
and fails when a guard object exists in only one of the two sources.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

# Every ``{s}`` is the target schema, quoted once at render time. It appears
# only in table references: NEW/OLD rows and keywords are never qualified.
GUARD_FUNCTIONS: dict[str, str] = {
    # 0093 - dispatch receipts are immutable evidence.
    "reject_llm_receipt_mutation": " BEGIN RAISE EXCEPTION 'Dispatch receipts are immutable' USING ERRCODE = '55000'; END; ",
    # 0094 - outcome evidence is immutable, and every row must bind to a receipt.
    "reject_llm_outcome_mutation": " BEGIN RAISE EXCEPTION 'Outcome evidence is immutable' USING ERRCODE = '55000'; END; ",
    "check_llm_outcome_receipt": (
        " DECLARE receipt RECORD; BEGIN "
        "SELECT tenant_id, pricing_policy_version, prepared_at, input_cost_per_1k, output_cost_per_1k, "
        "cache_read_cost_per_1m, cache_write_5m_cost_per_1m, cache_write_1h_cost_per_1m, pricing_token_unit "
        "INTO receipt FROM {s}.llm_dispatch_receipts WHERE dispatch_id = NEW.dispatch_id; "
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
        "RETURN NEW; END; "
    ),
    # 0095 - budget reservations are immutable and bind to their receipt tariff.
    "reject_llm_budget_reservation_mutation": (
        " BEGIN RAISE EXCEPTION 'Budget reservation is immutable' USING ERRCODE = '55000'; END; "
    ),
    "check_llm_budget_reservation_receipt": (
        " DECLARE receipt RECORD; BEGIN "
        "SELECT tenant_id, utc_day, tariff_state INTO receipt "
        "FROM {s}.llm_dispatch_receipts WHERE dispatch_id = NEW.dispatch_id; "
        "IF NOT FOUND OR receipt.tenant_id IS DISTINCT FROM NEW.tenant_id "
        "OR receipt.utc_day IS DISTINCT FROM NEW.utc_day THEN "
        "RAISE EXCEPTION 'Reservation receipt binding differs' USING ERRCODE = '23503'; END IF; "
        "IF (receipt.tariff_state = 'undefined' AND NEW.ceiling_state <> 'undefined') OR "
        "(receipt.tariff_state = 'free' AND (NEW.ceiling_state <> 'bounded' OR NEW.ceiling_usd IS DISTINCT FROM 0 "
        "OR NEW.bound_version IS NOT NULL)) OR "
        "(receipt.tariff_state = 'known' AND (NEW.ceiling_state = 'undefined' OR "
        "(NEW.ceiling_state = 'bounded' AND (NEW.ceiling_usd IS NULL OR NEW.ceiling_usd <= 0)))) THEN "
        "RAISE EXCEPTION 'Reservation tariff basis differs' USING ERRCODE = '23514'; END IF; "
        "RETURN NEW; END; "
    ),
    # 0097 - personal billing foundation.
    "reject_personal_llm_dispatches_mutation": (
        " BEGIN RAISE EXCEPTION 'Personal dispatch attribution is immutable' USING ERRCODE = '55000'; END; "
    ),
    "reject_task_billing_payers_mutation": (
        " BEGIN RAISE EXCEPTION 'Task payer consents are immutable' USING ERRCODE = '55000'; END; "
    ),
    "check_task_billing_payer_consent": (
        " DECLARE task_tenant integer; account_ready boolean; owner_active boolean; latest_version integer; BEGIN "
        "SELECT tenant_id INTO task_tenant FROM {s}.agent_tasks WHERE id = NEW.agent_task_id; "
        "IF task_tenant IS DISTINCT FROM NEW.tenant_id THEN "
        "RAISE EXCEPTION 'Task payer consent scope differs' USING ERRCODE = '23503'; END IF; "
        "SELECT a.is_active AND a.configured_at IS NOT NULL, u.is_active INTO account_ready, owner_active "
        "FROM {s}.personal_billing_accounts a JOIN {s}.users u ON u.id = a.user_id "
        "WHERE a.user_id = NEW.payer_user_id; "
        "IF account_ready IS DISTINCT FROM TRUE OR owner_active IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Task payer has no configured personal account' USING ERRCODE = '23514'; END IF; "
        "SELECT max(consent_version) INTO latest_version FROM {s}.task_billing_payers "
        "WHERE agent_task_id = NEW.agent_task_id; "
        "IF NEW.consent_version IS DISTINCT FROM coalesce(latest_version, 0) + 1 THEN "
        "RAISE EXCEPTION 'Task payer consent version is not next' USING ERRCODE = '23514'; END IF; "
        "RETURN NEW; END; "
    ),
    # 0097 with the 0103 operator-initiator clause, which is the current body.
    "check_personal_llm_dispatch_account": (
        " DECLARE valid boolean; task_tenant integer; task_payer integer; initiator_active boolean; "
        "member_active boolean; admin_active boolean; operator_active boolean; "
        "receipt_tenant integer; receipt_model integer; receipt_provider integer; BEGIN "
        "SELECT tenant_id, llm_model_row_id, llm_provider_row_id INTO receipt_tenant, receipt_model, receipt_provider "
        "FROM {s}.llm_dispatch_receipts WHERE dispatch_id = NEW.dispatch_id; "
        "IF receipt_tenant IS DISTINCT FROM NEW.tenant_id OR receipt_model IS DISTINCT FROM NEW.llm_model_row_id "
        "OR receipt_provider IS DISTINCT FROM NEW.llm_provider_row_id THEN "
        "RAISE EXCEPTION 'Personal dispatch must match its legacy receipt identity' USING ERRCODE = '23514'; END IF; "
        "SELECT a.is_active AND a.configured_at IS NOT NULL AND a.user_id = NEW.payer_user_id AND u.is_active "
        "INTO valid FROM {s}.personal_billing_accounts a "
        "JOIN {s}.users u ON u.id = a.user_id WHERE a.id = NEW.account_id; "
        "IF valid IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Dispatch payer account differs or is inactive' USING ERRCODE = '23514'; END IF; "
        "IF NEW.initiated_by_user_id IS NOT NULL THEN "
        "SELECT is_active INTO initiator_active FROM {s}.users WHERE id = NEW.initiated_by_user_id; "
        "IF initiator_active IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Dispatch initiator is inactive' USING ERRCODE = '23514'; END IF; END IF; "
        "IF NEW.agent_task_id IS NULL AND NEW.initiated_by_user_id IS DISTINCT FROM NEW.payer_user_id THEN "
        "RAISE EXCEPTION 'Non-task dispatch initiator must be the payer' USING ERRCODE = '23514'; END IF; "
        "IF NEW.agent_task_id IS NULL AND NEW.purpose = 'admin_probe' THEN "
        "SELECT EXISTS (SELECT 1 FROM {s}.users u JOIN {s}.roles r ON r.id = u.role_id "
        "WHERE u.id = NEW.payer_user_id AND u.is_active AND r.codename = 'admin') INTO admin_active; "
        "IF admin_active IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Admin probe payer must be active platform admin' USING ERRCODE = '23514'; END IF; "
        "END IF; "
        "IF NEW.agent_task_id IS NULL AND NEW.purpose <> 'admin_probe' THEN "
        "SELECT EXISTS (SELECT 1 FROM {s}.tenant_users m WHERE m.tenant_id = NEW.tenant_id "
        "AND m.user_id = NEW.payer_user_id AND m.is_active) INTO member_active; "
        "IF member_active IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Non-task payer workspace membership is inactive' USING ERRCODE = '23514'; END IF; END IF; "
        "IF NEW.agent_task_id IS NOT NULL THEN "
        "SELECT tenant_id INTO task_tenant FROM {s}.agent_tasks WHERE id = NEW.agent_task_id; "
        "IF task_tenant IS DISTINCT FROM NEW.tenant_id THEN "
        "RAISE EXCEPTION 'Dispatch task workspace differs' USING ERRCODE = '23503'; END IF; "
        "SELECT payer_user_id INTO task_payer FROM {s}.task_billing_payers "
        "WHERE agent_task_id = NEW.agent_task_id AND tenant_id = NEW.tenant_id "
        "ORDER BY consent_version DESC LIMIT 1; "
        "IF task_payer IS DISTINCT FROM NEW.payer_user_id THEN "
        "RAISE EXCEPTION 'Dispatch task payer lacks current consent' USING ERRCODE = '23514'; END IF; "
        "SELECT EXISTS (SELECT 1 FROM {s}.tenant_users m WHERE m.tenant_id = NEW.tenant_id "
        "AND m.user_id = NEW.payer_user_id AND m.is_active) INTO member_active; "
        "IF member_active IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Task payer workspace membership is inactive' USING ERRCODE = '23514'; END IF; "
        "IF NEW.initiated_by_user_id IS NOT NULL THEN "
        "SELECT EXISTS (SELECT 1 FROM {s}.tenant_users m WHERE m.tenant_id = NEW.tenant_id "
        "AND m.user_id = NEW.initiated_by_user_id AND m.is_active) INTO member_active; "
        "SELECT EXISTS (SELECT 1 FROM {s}.users u JOIN {s}.roles r ON r.id = u.role_id "
        "WHERE u.id = NEW.initiated_by_user_id AND u.is_active AND r.codename IN ('operator', 'admin')) "
        "INTO operator_active; "
        "IF member_active IS DISTINCT FROM TRUE AND operator_active IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Task initiator workspace membership is inactive' USING ERRCODE = '23514'; END IF; "
        "END IF; END IF; "
        "RETURN NEW; END; "
    ),
    # 0099 - personal billing ledger.
    "reject_personal_llm_bound_policies_mutation": (
        " BEGIN RAISE EXCEPTION 'Bound policies are immutable' USING ERRCODE = '55000'; END; "
    ),
    "reject_personal_llm_reservations_mutation": (
        " BEGIN RAISE EXCEPTION 'Personal reservations are immutable' USING ERRCODE = '55000'; END; "
    ),
    "reject_personal_llm_ledger_entries_mutation": (
        " BEGIN RAISE EXCEPTION 'Personal ledger entries are immutable' USING ERRCODE = '55000'; END; "
    ),
    "reject_personal_llm_billing_incidents_mutation": (
        " BEGIN RAISE EXCEPTION 'Billing incidents are immutable' USING ERRCODE = '55000'; END; "
    ),
    "reject_personal_billing_cutovers_mutation": (
        " BEGIN RAISE EXCEPTION 'Billing cutovers are immutable' USING ERRCODE = '55000'; END; "
    ),
    "check_personal_llm_bound_policy": (
        " DECLARE model_provider integer; model_snapshot varchar(200); provider_snapshot varchar(200); "
        "reviewer_active boolean; BEGIN "
        "SELECT m.provider_id, m.model_id, p.name INTO model_provider, model_snapshot, provider_snapshot "
        "FROM {s}.llm_models m JOIN {s}.llm_providers p ON p.id = m.provider_id WHERE m.id = NEW.model_row_id; "
        "IF model_provider IS DISTINCT FROM NEW.provider_row_id OR model_snapshot IS DISTINCT FROM NEW.model_id "
        "OR provider_snapshot IS DISTINCT FROM NEW.provider_name THEN "
        "RAISE EXCEPTION 'Bound policy provider/model mismatch' USING ERRCODE = '23514'; END IF; "
        "SELECT u.is_active AND r.codename = 'admin' INTO reviewer_active "
        "FROM {s}.users u JOIN {s}.roles r ON r.id = u.role_id WHERE u.id = NEW.reviewed_by_user_id; "
        "IF reviewer_active IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Bound policy reviewer must be active platform admin' USING ERRCODE = '23514'; END IF; "
        "RETURN NEW; END; "
    ),
    # 0099 body replaced by 0102: unambiguously named locals.
    "check_personal_llm_reservation": (
        " DECLARE a_limit numeric; prepared timestamptz; dispatch_account integer; cutover timestamptz; "
        "provider_id integer; dispatch_model_row_id integer; dispatch_provider_name varchar(200); "
        "dispatch_model_id varchar(200); policy_found boolean; BEGIN "
        "SELECT a.daily_limit_usd, d.prepared_at, d.account_id, c.effective_at, d.llm_provider_row_id, "
        "d.llm_model_row_id, d.provider_name, d.model_id "
        "INTO a_limit, prepared, dispatch_account, cutover, provider_id, dispatch_model_row_id, "
        "dispatch_provider_name, dispatch_model_id "
        "FROM {s}.personal_llm_dispatches d "
        "JOIN {s}.personal_billing_accounts a ON a.id = d.account_id "
        "LEFT JOIN {s}.personal_billing_cutovers c ON c.account_id = d.account_id "
        "WHERE d.dispatch_id = NEW.dispatch_id; "
        "IF dispatch_account IS DISTINCT FROM NEW.account_id OR prepared IS NULL "
        "OR NEW.utc_day IS DISTINCT FROM (prepared AT TIME ZONE 'UTC')::date OR cutover IS NULL "
        "OR prepared < cutover THEN "
        "RAISE EXCEPTION 'Personal reservation has no active reviewed cutover/dispatch binding' "
        "USING ERRCODE = '23514'; END IF; "
        "IF a_limit IS NULL OR (a_limit = 0 AND NEW.cap_usd IS NOT NULL) "
        "OR (a_limit > 0 AND NEW.cap_usd IS DISTINCT FROM a_limit) THEN "
        "RAISE EXCEPTION 'Reservation cap differs from configured account policy' USING ERRCODE = '23514'; END IF; "
        "IF NEW.ceiling_state = 'bounded' AND NEW.ceiling_usd > 0 THEN "
        "SELECT EXISTS (SELECT 1 FROM {s}.personal_llm_bound_policies p "
        "WHERE p.provider_row_id = provider_id AND p.model_row_id = dispatch_model_row_id "
        "AND p.provider_name = dispatch_provider_name AND p.model_id = dispatch_model_id "
        "AND p.version = NEW.bound_version) INTO policy_found; "
        "IF policy_found IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Reservation bound is not a reviewed policy' USING ERRCODE = '23514'; END IF; "
        "END IF; RETURN NEW; END; "
    ),
    "check_personal_llm_reservation_state_event": (
        " DECLARE event_dispatch varchar(32); event_tenant integer; dispatch_tenant integer; BEGIN "
        "IF NEW.latest_event_id IS NULL THEN RETURN NEW; END IF; "
        "SELECT dispatch_id, tenant_id INTO event_dispatch, event_tenant "
        "FROM {s}.llm_outcome_events WHERE event_id = NEW.latest_event_id; "
        "SELECT tenant_id INTO dispatch_tenant FROM {s}.personal_llm_dispatches "
        "WHERE dispatch_id = NEW.dispatch_id; "
        "IF event_dispatch IS DISTINCT FROM NEW.dispatch_id OR event_tenant IS DISTINCT FROM dispatch_tenant THEN "
        "RAISE EXCEPTION 'Reservation state event differs from its dispatch workspace' USING ERRCODE = '23514'; "
        "END IF; RETURN NEW; END; "
    ),
    "check_personal_llm_ledger_entry": (
        " DECLARE valid boolean; event_dispatch varchar(32); event_tenant integer; dispatch_tenant integer; BEGIN "
        "SELECT (r.account_id = NEW.account_id AND r.utc_day = NEW.utc_day) INTO valid "
        "FROM {s}.personal_llm_reservations r WHERE r.dispatch_id = NEW.dispatch_id; "
        "IF valid IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Ledger entry differs from its reservation' USING ERRCODE = '23514'; END IF; "
        "IF NEW.event_id IS NOT NULL THEN "
        "SELECT dispatch_id, tenant_id INTO event_dispatch, event_tenant "
        "FROM {s}.llm_outcome_events WHERE event_id = NEW.event_id; "
        "SELECT tenant_id INTO dispatch_tenant FROM {s}.personal_llm_dispatches "
        "WHERE dispatch_id = NEW.dispatch_id; "
        "IF event_dispatch IS DISTINCT FROM NEW.dispatch_id OR event_tenant IS NULL "
        "OR dispatch_tenant IS DISTINCT FROM event_tenant THEN "
        "RAISE EXCEPTION 'Ledger outcome does not match its dispatch workspace' USING ERRCODE = '23514'; END IF; "
        "END IF; RETURN NEW; END; "
    ),
    "check_personal_llm_billing_incident": (
        " DECLARE event_dispatch varchar(32); event_tenant integer; dispatch_tenant integer; "
        "reservation_account integer; reservation_day date; BEGIN "
        "SELECT dispatch_id, tenant_id INTO event_dispatch, event_tenant "
        "FROM {s}.llm_outcome_events WHERE event_id = NEW.event_id; "
        "SELECT account_id, utc_day INTO reservation_account, reservation_day "
        "FROM {s}.personal_llm_reservations WHERE dispatch_id = NEW.dispatch_id; "
        "SELECT tenant_id INTO dispatch_tenant FROM {s}.personal_llm_dispatches "
        "WHERE dispatch_id = NEW.dispatch_id; "
        "IF event_dispatch IS DISTINCT FROM NEW.dispatch_id "
        "OR reservation_account IS DISTINCT FROM NEW.account_id "
        "OR reservation_day IS DISTINCT FROM NEW.utc_day "
        "OR event_tenant IS DISTINCT FROM dispatch_tenant THEN "
        "RAISE EXCEPTION 'Billing incident differs from its stored event/reservation' USING ERRCODE = '23514'; "
        "END IF; RETURN NEW; END; "
    ),
    "check_personal_billing_cutover": (
        " DECLARE ready boolean; reviewer boolean; BEGIN "
        "SELECT is_active AND configured_at IS NOT NULL INTO ready "
        "FROM {s}.personal_billing_accounts WHERE id = NEW.account_id; "
        "SELECT u.is_active AND r.codename = 'admin' INTO reviewer "
        "FROM {s}.users u JOIN {s}.roles r ON r.id = u.role_id WHERE u.id = NEW.reviewed_by_user_id; "
        "IF ready IS DISTINCT FROM TRUE OR reviewer IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Cutover requires configured account and active platform reviewer' "
        "USING ERRCODE = '23514'; END IF; RETURN NEW; END; "
    ),
    # 0100 - reviewed legacy billing attribution.
    "reject_personal_legacy_attribution_consents_mutation": (
        " BEGIN RAISE EXCEPTION 'Legacy billing attribution evidence is immutable' USING ERRCODE = '55000'; END; "
    ),
    "reject_personal_legacy_attributions_mutation": (
        " BEGIN RAISE EXCEPTION 'Legacy billing attribution evidence is immutable' USING ERRCODE = '55000'; END; "
    ),
    "check_personal_legacy_attribution_consent": (
        " DECLARE account_owner integer; source_tenant integer; owner_active boolean; member_active boolean; BEGIN "
        "SELECT user_id INTO account_owner FROM {s}.personal_billing_accounts WHERE id = NEW.account_id; "
        "IF account_owner IS DISTINCT FROM NEW.consented_by_user_id THEN "
        "RAISE EXCEPTION 'Legacy attribution consent must come from the account owner' "
        "USING ERRCODE = '23514'; END IF; "
        "SELECT is_active INTO owner_active FROM {s}.users WHERE id = NEW.consented_by_user_id; "
        "IF owner_active IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Consent owner is inactive' USING ERRCODE = '23514'; END IF; "
        "IF NEW.dispatch_id IS NOT NULL THEN "
        "SELECT tenant_id INTO source_tenant FROM {s}.llm_dispatch_receipts WHERE dispatch_id = NEW.dispatch_id; "
        "ELSE SELECT tenant_id INTO source_tenant FROM {s}.llm_attempts WHERE attempt_key = NEW.attempt_key; END IF; "
        "IF source_tenant IS DISTINCT FROM NEW.tenant_id THEN "
        "RAISE EXCEPTION 'Legacy identity workspace differs' USING ERRCODE = '23514'; END IF; "
        "SELECT EXISTS (SELECT 1 FROM {s}.tenant_users m WHERE m.tenant_id = NEW.tenant_id "
        "AND m.user_id = NEW.consented_by_user_id AND m.is_active) INTO member_active; "
        "IF member_active IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Consent owner has no active workspace membership' USING ERRCODE = '23514'; END IF; "
        "RETURN NEW; END; "
    ),
    "check_personal_legacy_attribution_review": (
        " DECLARE consent record; reviewer_active boolean; BEGIN "
        "SELECT * INTO consent FROM {s}.personal_legacy_attribution_consents WHERE id = NEW.consent_id; "
        "IF consent.id IS NULL OR consent.source_key IS DISTINCT FROM NEW.source_key "
        "OR consent.dispatch_id IS DISTINCT FROM NEW.dispatch_id "
        "OR consent.attempt_key IS DISTINCT FROM NEW.attempt_key "
        "OR consent.tenant_id IS DISTINCT FROM NEW.tenant_id "
        "OR consent.account_id IS DISTINCT FROM NEW.account_id "
        "OR consent.consented_by_user_id IS DISTINCT FROM NEW.consented_by_user_id THEN "
        "RAISE EXCEPTION 'Reviewed legacy mapping differs from owner consent' USING ERRCODE = '23514'; END IF; "
        "SELECT u.is_active AND r.codename = 'admin' INTO reviewer_active "
        "FROM {s}.users u JOIN {s}.roles r ON r.id = u.role_id WHERE u.id = NEW.reviewed_by_user_id; "
        "IF reviewer_active IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Legacy mapping reviewer must be active platform admin' USING ERRCODE = '23514'; END IF; "
        "RETURN NEW; END; "
    ),
    # 0109 - personal catalog ownership and workspace task binding.
    "guard_personal_catalog_owner": (
        "\n        BEGIN\n"
        "          IF NEW.owner_user_id<>OLD.owner_user_id THEN\n"
        "            RAISE EXCEPTION 'Personal catalog ownership is immutable' USING ERRCODE='23514';\n"
        "          END IF;\n"
        "          IF TG_TABLE_NAME='llm_models' THEN\n"
        "            IF NEW.provider_id<>OLD.provider_id THEN\n"
        "              RAISE EXCEPTION 'Model provider is immutable' USING ERRCODE='23514';\n"
        "            END IF;\n"
        "          END IF;\n"
        "          RETURN NEW;\n"
        "        END "
    ),
    "guard_task_binding": (
        "\n        DECLARE task_tenant integer; source_tenant integer; source_owner integer;\n"
        "        BEGIN\n"
        "          IF TG_TABLE_NAME='agent_task_sources' THEN\n"
        "            SELECT tenant_id INTO task_tenant FROM {s}.agent_tasks "
        "WHERE id=NEW.agent_task_id FOR KEY SHARE;\n"
        "            SELECT tenant_id, owner_task_id INTO source_tenant, source_owner FROM {s}.sources "
        "WHERE id=NEW.source_id FOR SHARE;\n"
        "            IF task_tenant IS NULL OR source_tenant IS NULL OR task_tenant<>source_tenant\n"
        "               OR (source_owner IS NOT NULL AND source_owner<>NEW.agent_task_id) THEN\n"
        "              RAISE EXCEPTION 'Invalid workspace task source binding' USING ERRCODE='23514';\n"
        "            END IF;\n"
        "          ELSIF TG_TABLE_NAME='agent_tasks' THEN\n"
        "            IF TG_OP='UPDATE' AND NEW.tenant_id<>OLD.tenant_id THEN\n"
        "              RAISE EXCEPTION 'Workspace ownership is immutable' USING ERRCODE='23514';\n"
        "            END IF;\n"
        "            IF NEW.agent_scenario_id IS NOT NULL THEN\n"
        "              PERFORM 1 FROM {s}.agent_scenarios "
        "WHERE id=NEW.agent_scenario_id AND tenant_id=NEW.tenant_id FOR KEY SHARE;\n"
        "              IF NOT FOUND THEN RAISE EXCEPTION 'Invalid workspace scenario binding' "
        "USING ERRCODE='23514'; END IF;\n"
        "            END IF;\n"
        "          ELSE\n"
        "            IF TG_TABLE_NAME='sources' THEN\n"
        "              IF NEW.owner_task_id IS NOT NULL THEN\n"
        "                PERFORM 1 FROM {s}.agent_tasks "
        "WHERE id=NEW.owner_task_id AND tenant_id=NEW.tenant_id FOR KEY SHARE;\n"
        "                IF NOT FOUND THEN RAISE EXCEPTION 'Invalid source owning task' "
        "USING ERRCODE='23514'; END IF;\n"
        "                IF EXISTS (SELECT 1 FROM {s}.agent_task_sources "
        "WHERE source_id=NEW.id AND agent_task_id<>NEW.owner_task_id) THEN\n"
        "                  RAISE EXCEPTION 'Task-owned source has foreign links' USING ERRCODE='23514';\n"
        "                END IF;\n"
        "              END IF;\n"
        "            END IF;\n"
        "            IF TG_OP='UPDATE' THEN\n"
        "              IF NEW.tenant_id<>OLD.tenant_id THEN\n"
        "                RAISE EXCEPTION 'Workspace ownership is immutable' USING ERRCODE='23514';\n"
        "              END IF;\n"
        "            END IF;\n"
        "          END IF;\n"
        "          RETURN NEW;\n"
        "        END "
    ),
}


@dataclass(frozen=True)
class GuardTrigger:
    """One ``CREATE TRIGGER`` as the migrations leave it.

    ``events`` and ``columns`` keep migration order because PostgreSQL records
    ``UPDATE OF a, b`` as given; ``pg_get_triggerdef`` renders ``DELETE OR UPDATE``
    for the same trigger.
    """

    table: str
    name: str
    function: str
    events: tuple[str, ...]
    columns: tuple[str, ...] = ()
    for_each_row: bool = True

    def definition(self, schema: str) -> str:
        """Schema-qualified ``CREATE TRIGGER`` executing a function in ``schema``."""
        of = f" OF {', '.join(f'\"{column}\"' for column in self.columns)}" if self.columns else ""
        events = " OR ".join(self.events)
        level = "FOR EACH ROW" if self.for_each_row else "FOR EACH STATEMENT"
        qualified = f'"{schema}"'
        return (
            f'CREATE TRIGGER "{self.name}" BEFORE {events}{of} ON {qualified}."{self.table}" '
            f'{level} EXECUTE FUNCTION {qualified}."{self.function}"()'
        )


# Immutable-evidence and consent tables that migrations 0097-0100 guard in bulk.
_IMMUTABLE_TABLES: tuple[tuple[str, str, str], ...] = (
    # (table, mutation exception kind, extra validator, or "")
    ("personal_llm_dispatches", "Personal dispatch attribution is immutable", "check_personal_llm_dispatch_account"),
    ("task_billing_payers", "Task payer consents are immutable", "check_task_billing_payer_consent"),
    ("personal_llm_bound_policies", "Bound policies are immutable", "check_personal_llm_bound_policy"),
    ("personal_llm_reservations", "Personal reservations are immutable", "check_personal_llm_reservation"),
    ("personal_llm_ledger_entries", "Personal ledger entries are immutable", "check_personal_llm_ledger_entry"),
    ("personal_llm_billing_incidents", "Billing incidents are immutable", "check_personal_llm_billing_incident"),
    ("personal_billing_cutovers", "Billing cutovers are immutable", "check_personal_billing_cutover"),
    (
        "personal_legacy_attribution_consents",
        "Legacy billing attribution evidence is immutable",
        "check_personal_legacy_attribution_consent",
    ),
    (
        "personal_legacy_attributions",
        "Legacy billing attribution evidence is immutable",
        "check_personal_legacy_attribution_review",
    ),
)


def _immutable_guards() -> list[GuardTrigger]:
    """The ``<stem>_immutable``/``<stem>_no_truncate``/``<stem>_validate`` triple."""
    triggers: list[GuardTrigger] = []
    for table, _kind, validator in _IMMUTABLE_TABLES:
        reject = f"reject_{table}_mutation"
        triggers.append(
            GuardTrigger(table=table, name=f"{table}_immutable", function=reject, events=("UPDATE", "DELETE"))
        )
        triggers.append(
            GuardTrigger(
                table=table,
                name=f"{table}_no_truncate",
                function=reject,
                events=("TRUNCATE",),
                for_each_row=False,
            )
        )
        triggers.append(GuardTrigger(table=table, name=f"{table}_validate", function=validator, events=("INSERT",)))
    return triggers


GUARD_TRIGGERS: tuple[GuardTrigger, ...] = (
    # 0093
    GuardTrigger(
        table="llm_dispatch_receipts",
        name="llm_dispatch_receipts_immutable",
        function="reject_llm_receipt_mutation",
        events=("UPDATE", "DELETE"),
    ),
    GuardTrigger(
        table="llm_dispatch_receipts",
        name="llm_dispatch_receipts_no_truncate",
        function="reject_llm_receipt_mutation",
        events=("TRUNCATE",),
        for_each_row=False,
    ),
    # 0094
    GuardTrigger(
        table="llm_outcome_events",
        name="llm_outcome_events_immutable",
        function="reject_llm_outcome_mutation",
        events=("UPDATE", "DELETE"),
    ),
    GuardTrigger(
        table="llm_outcome_events",
        name="llm_outcome_events_no_truncate",
        function="reject_llm_outcome_mutation",
        events=("TRUNCATE",),
        for_each_row=False,
    ),
    GuardTrigger(
        table="llm_outcome_events",
        name="llm_outcome_events_receipt_binding",
        function="check_llm_outcome_receipt",
        events=("INSERT",),
    ),
    # 0095
    GuardTrigger(
        table="llm_budget_reservations",
        name="llm_budget_reservations_immutable",
        function="reject_llm_budget_reservation_mutation",
        events=("UPDATE", "DELETE"),
    ),
    GuardTrigger(
        table="llm_budget_reservations",
        name="llm_budget_reservations_no_truncate",
        function="reject_llm_budget_reservation_mutation",
        events=("TRUNCATE",),
        for_each_row=False,
    ),
    GuardTrigger(
        table="llm_budget_reservations",
        name="llm_budget_reservations_receipt_binding",
        function="check_llm_budget_reservation_receipt",
        events=("INSERT",),
    ),
    # 0099
    GuardTrigger(
        table="personal_llm_reservation_states",
        name="personal_llm_reservation_states_event_validate",
        function="check_personal_llm_reservation_state_event",
        events=("INSERT", "UPDATE"),
    ),
    # 0097-0100 bulk guards
    *_immutable_guards(),
    # 0109
    GuardTrigger(
        table="llm_providers",
        name="guard_personal_catalog_owner",
        function="guard_personal_catalog_owner",
        events=("UPDATE",),
    ),
    GuardTrigger(
        table="llm_models",
        name="guard_personal_catalog_owner",
        function="guard_personal_catalog_owner",
        events=("UPDATE",),
    ),
    GuardTrigger(
        table="agent_task_sources",
        name="guard_task_source_binding",
        function="guard_task_binding",
        events=("INSERT", "UPDATE"),
    ),
    GuardTrigger(
        table="agent_tasks",
        name="guard_task_scenario_binding",
        function="guard_task_binding",
        events=("INSERT", "UPDATE"),
    ),
    GuardTrigger(
        table="sources",
        name="guard_workspace_ownership",
        function="guard_task_binding",
        events=("INSERT", "UPDATE"),
        columns=("tenant_id", "owner_task_id"),
    ),
    GuardTrigger(
        table="agent_scenarios",
        name="guard_workspace_ownership",
        function="guard_task_binding",
        events=("UPDATE",),
        columns=("tenant_id",),
    ),
)

# Names the migrations also drop and recreate (0102 replaces the reservation
# validator trigger). Kept explicit so a drift test can tell an intentional
# replacement from a stray DROP.
GUARD_TRIGGER_REPLACEMENTS: frozenset[str] = frozenset({"personal_llm_reservations_validate"})

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _schema(schema: str) -> str:
    """Quote a plain schema identifier; refuse anything a copy-paste could smuggle."""
    if not _IDENTIFIER.match(schema):
        raise ValueError(f"Refusing unsafe schema name for guard DDL: {schema!r}")
    return f'"{schema}"'


def function_ddl(name: str, schema: str) -> str:
    """``CREATE OR REPLACE FUNCTION`` for one guard, executable in ``schema``."""
    body = GUARD_FUNCTIONS[name].replace("{s}", _schema(schema))
    return (
        f'CREATE OR REPLACE FUNCTION {_schema(schema)}."{name}"() '
        f"RETURNS trigger LANGUAGE plpgsql AS $guard$\n{body}\n$guard$"
    )


def ddl(schema: str) -> list[str]:
    """Every guard statement for ``schema``, functions first.

    The create path runs this after ``create_all``; ``DROP TRIGGER IF EXISTS``
    makes it re-runnable, because ``CREATE TRIGGER`` has no ``IF NOT EXISTS``.
    """
    _schema(schema)
    statements = [function_ddl(name, schema) for name in GUARD_FUNCTIONS]
    for trigger in GUARD_TRIGGERS:
        if trigger.function not in GUARD_FUNCTIONS:
            raise ValueError(f"Trigger {trigger.name} executes an unknown guard function {trigger.function!r}")
        statements.append(f'DROP TRIGGER IF EXISTS "{trigger.name}" ON {_schema(schema)}."{trigger.table}"')
        statements.append(trigger.definition(schema))
    return statements


def apply_schema_guards(conn, schema: str, text: Callable[[str], object]) -> None:
    """Execute every guard statement for ``schema`` on SQLAlchemy ``conn``.

    Idempotent: ``CREATE OR REPLACE FUNCTION`` replaces a body and the trigger
    is dropped and recreated, so a second call leaves the same catalog objects.
    The caller owns the transaction; a failure here must abort it, never be
    swallowed (a schema without guards silently accepts evidence mutations).

    ``text`` is the caller's SQL wrapper (``sqlalchemy.text``): this module stays
    free of driver imports so the offline CI lane can load it.
    """
    for statement in ddl(schema):
        conn.execute(text(statement))


def names() -> Sequence[str]:
    """Both object kinds, sorted, for diagnostics."""
    return sorted([*GUARD_FUNCTIONS, *(trigger.name for trigger in GUARD_TRIGGERS)])
