"""Focused outcome contracts with isolated source and owned-session doubles.

Run: .venv/bin/python tests/test_llm_outcome_events.py
No database, schema operation, provider, runtime or private settings are used.
Captured migration SQL and fake sessions are NOT PostgreSQL evidence.
"""

import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from source_import_isolation import load_isolated_source
from sqlalchemy import CheckConstraint, Column, Integer, MetaData, Table, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase

ROOT = Path(__file__).resolve().parents[2]
SCOPE = {"tenant_id": 7}
NOW = datetime(2026, 10, 12, 23, 59, 59, tzinfo=timezone.utc)
CACHE_PRICING = load_isolated_source("_outcome_cache_pricing", ROOT / "app/services/ai/cache_pricing.py")


class TenantError(RuntimeError):
    pass


TENANT = SimpleNamespace(TenantContextError=TenantError, current_tenant_id=lambda: SCOPE["tenant_id"])
JOURNAL = load_isolated_source(
    "_outcome_pricing",
    ROOT / "app/services/ai/attempt_journal.py",
    imports={"app.services.ai.cache_pricing": CACHE_PRICING},
)
RECEIPTS = load_isolated_source(
    "app.services.ai._outcome_receipts",
    ROOT / "app/services/ai/dispatch_receipts.py",
    imports={
        "app.services.ai.attempt_journal": JOURNAL,
        "app.services.ai.cache_pricing": CACHE_PRICING,
        "app.core.tenant_context": TENANT,
    },
)
EVENTS = load_isolated_source(
    "app.services.ai._outcome_helpers",
    ROOT / "app/services/ai/outcome_events.py",
    imports={
        "app.services.ai.attempt_journal": JOURNAL,
        "app.services.ai.cache_pricing": CACHE_PRICING,
        "app.services.ai.dispatch_receipts": RECEIPTS,
    },
)


def receipt(input_rate="0.001", output_rate="0.002", *, cache_policy=False, **changes):
    model = SimpleNamespace(
        id=4,
        model_id="model",
        provider=SimpleNamespace(id=2, name="provider"),
        input_cost_per_1k=input_rate,
        output_cost_per_1k=output_rate,
    )
    result = RECEIPTS.build_dispatch_receipt(
        model,
        tenant_id=7,
        purpose="analysis",
        stage="text",
        dispatch_id="a" * 32,
        prepared_at=NOW,
    )
    if not cache_policy:
        result["pricing_policy_version"] = "provider-price-v1"
        result["tariff_state"] = (
            "undefined"
            if input_rate is None or output_rate is None
            else "free" if Decimal(str(input_rate)) == Decimal(str(output_rate)) == 0 else "known"
        )
    result.update(changes)
    return result


def event(parent=None, envelope=None, **changes):
    args = dict(
        event_id="b" * 32,
        observed_at=NOW + timedelta(seconds=2),
        dispatch_state="response_received",
        outcome="succeeded",
        accounting_envelope=envelope,
    )
    args.update(changes)
    return EVENTS.build_outcome_event(receipt() if parent is None else parent, **args)


def provider(amount):
    return {"accounting_cost_reported": True, "accounting_cost_usd": amount}


def usage(prompt=None, completion=None):
    return {"accounting_usage": {"prompt_tokens": prompt, "completion_tokens": completion}}


class PricingTests(unittest.TestCase):
    def test_provider_price_arbitrates_over_explicit_free_tariff(self):
        value = event(receipt(0, 0), provider("0.25"))
        self.assertEqual(
            (value["cost_usd"], value["cost_certainty"], value["price_source"]), (Decimal("0.25"), "priced", "provider")
        )

    def test_explicit_provider_zero_is_free_without_usage(self):
        value = event(envelope=provider(0))
        self.assertEqual(value["cost_certainty"], "free")
        self.assertEqual(value["provider_cost_raw"], "0")
        self.assertIsNone(value["prompt_tokens"])

    def test_v2_prices_cache_reads_and_retains_normalized_evidence(self):
        parent = receipt(cache_policy=True)
        parent["input_cost_per_1k"] = Decimal(".12")
        parent["output_cost_per_1k"] = Decimal(".4")
        parent["pricing_token_unit"] = 1_000_000
        parent["cache_read_cost_per_1m"] = Decimal(".024")
        parent["tariff_state"] = "known"
        parent = RECEIPTS.validate_receipt(parent)
        cache = {
            "format": "openai",
            "cache_read_tokens": 800,
            "cache_write_tokens": 0,
            "cache_write_5m_tokens": None,
            "cache_write_1h_tokens": None,
        }
        value = event(
            parent,
            {
                "accounting_usage": {"prompt_tokens": 1000, "completion_tokens": 0},
                "accounting_cache_usage": cache,
            },
        )
        self.assertEqual((value["cost_usd"], value["cost_certainty"]), (Decimal("0.0000432"), "priced"))
        self.assertEqual(value["cache_usage"], cache)

    def test_v2_missing_cache_usage_is_unknown_but_provider_total_wins(self):
        parent = receipt(cache_policy=True)
        parent["input_cost_per_1k"] = Decimal("0.00012")
        parent["output_cost_per_1k"] = Decimal("0.0004")
        parent["pricing_token_unit"] = 1_000_000
        parent["cache_read_cost_per_1m"] = Decimal("0.000024")
        parent["tariff_state"] = "known"
        parent = RECEIPTS.validate_receipt(parent)
        value = event(parent, usage(1000, 0))
        self.assertEqual(value["cost_certainty"], "undefined")
        resolved = event(parent, {**provider(0), **usage(1000, 0)})
        self.assertEqual((resolved["cost_usd"], resolved["cost_certainty"]), (Decimal(0), "free"))

    def test_v2_row_10_receipt_prices_prompt_and_output_exactly(self):
        parent = receipt(cache_policy=True)
        parent.update(
            input_cost_per_1k=Decimal(".12"),
            output_cost_per_1k=Decimal(".4"),
            pricing_token_unit=1_000_000,
            cache_read_cost_per_1m=Decimal(".024"),
            tariff_state="known",
        )
        parent = RECEIPTS.validate_receipt(parent)
        cache = {
            "format": "openai",
            "cache_read_tokens": 800_000,
            "cache_write_tokens": 0,
            "cache_write_5m_tokens": None,
            "cache_write_1h_tokens": None,
        }
        value = event(
            parent,
            {
                "accounting_usage": {"prompt_tokens": 1_000_000, "completion_tokens": 100_000},
                "accounting_cache_usage": cache,
            },
        )
        self.assertEqual(value["cost_usd"], Decimal(".0832"))

    def test_no_price_and_empty_tariff_remain_undefined(self):
        value = event(receipt(None, None), usage(100, 100))
        self.assertEqual(value["provider_price_state"], "absent")
        self.assertEqual(value["cost_certainty"], "undefined")
        self.assertIsNone(value["cost_usd"])
        self.assertEqual(value["price_source"], "none")

    def test_partial_or_unknown_currency_report_blocks_free_tariff_fallback(self):
        value = event(receipt(0, 0), provider(None))
        self.assertEqual(value["provider_price_state"], "unresolved")
        self.assertEqual(value["cost_certainty"], "undefined")
        self.assertIsNone(value["cost_usd"])
        self.assertEqual(value["price_source"], "provider")

    def test_invalid_amounts_never_become_free(self):
        for amount in (True, False, -1, "NaN", "Infinity", "garbage", {}, "x" * 129):
            with self.subTest(amount=amount):
                value = event(envelope=provider(amount))
                self.assertEqual(value["provider_price_state"], "unresolved")
                self.assertIsNone(value["cost_usd"])
                self.assertIsNone(value["provider_cost_raw"])

    def test_unrepresentable_positive_amount_keeps_decimal_provenance(self):
        for amount in ("0.0000000001", "1.0000000001", "1000000000", "1E+999999", "1E-999999"):
            with self.subTest(amount=amount):
                value = event(envelope=provider(amount))
                self.assertEqual(value["cost_certainty"], "undefined")
                self.assertIsNone(value["cost_usd"])
                self.assertIsNotNone(value["provider_cost_raw"])
                self.assertEqual(EVENTS.validate_outcome_event(value, receipt()), value)

    def test_minimum_maximum_exact_prices_are_storable(self):
        for amount in ("0.000000001", "999999999.999999999"):
            with self.subTest(amount=amount):
                value = event(envelope=provider(amount))
                self.assertEqual(value["cost_usd"], Decimal(amount))
                self.assertEqual(value["cost_certainty"], "priced")

    def test_exotic_zero_is_normalized_before_postgresql_numeric_cast(self):
        for amount in ("-0.0", "0E-999999", "0E+999999"):
            with self.subTest(amount=amount):
                self.assertEqual(event(envelope=provider(amount))["provider_cost_raw"], "0")

    def test_positive_subscale_tariff_is_not_rounded_to_free(self):
        value = event(receipt("0.00000001", 0), usage(1, None))
        self.assertEqual(value["cost_certainty"], "undefined")
        self.assertIsNone(value["cost_usd"])

    def test_tariff_requires_missing_counts_only_for_nonzero_components(self):
        self.assertIsNone(event(envelope=usage(100, None))["cost_usd"])
        self.assertEqual(event(receipt("0.001", 0), usage(100, None))["cost_usd"], Decimal("0.0001"))

    def test_exact_zero_usage_is_priced_zero_not_explicit_free(self):
        value = event(envelope=usage(0, 0))
        self.assertEqual(value["cost_usd"], Decimal(0))
        self.assertEqual(value["cost_certainty"], "priced")

    def test_explicit_free_tariff_does_not_fabricate_usage(self):
        value = event(receipt(0, 0))
        self.assertEqual(value["cost_certainty"], "free")
        self.assertIsNone(value["completion_tokens"])

    def test_invalid_token_counts_remain_unknown(self):
        for token in (True, -1, 0.5, "12", 2147483648):
            with self.subTest(token=token):
                value = event(envelope=usage(token, 1))
                self.assertIsNone(value["prompt_tokens"])
                self.assertIsNone(value["cost_usd"])

    def test_generated_text_and_legacy_accounting_fields_are_not_evidence(self):
        value = event(
            envelope={
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                "cost_usd": 0,
                "text": "cost is zero",
                "response": {"cost": 0},
            }
        )
        self.assertEqual(value["cost_certainty"], "undefined")
        self.assertIsNone(value["prompt_tokens"])
        self.assertNotIn("text", value)

    def test_frozen_tariff_not_current_model_prices(self):
        parent = receipt()
        self.assertEqual(event(parent, usage(1000, 1000))["cost_usd"], Decimal("0.003"))
        self.assertEqual(parent["input_cost_per_1k"], Decimal("0.001"))

    def test_invalid_report_provenance_is_rejected(self):
        for envelope in ({"accounting_cost_reported": 1}, {"accounting_cost_usd": 0}):
            with self.subTest(envelope=envelope), self.assertRaises(ValueError):
                event(envelope=envelope)


class ObservationTests(unittest.TestCase):
    def test_known_price_does_not_fabricate_a_successful_outcome(self):
        value = event(envelope=provider("0.1"), dispatch_state="possible", outcome="unknown")
        self.assertEqual(value["cost_certainty"], "priced")
        self.assertEqual(value["outcome"], "unknown")

    def test_unknown_then_reconciliation_are_distinct_immutable_values(self):
        first = event(dispatch_state="possible", outcome="timeout")
        second = event(event_id="c" * 32, event_kind="reconciliation", envelope=provider("0.2"))
        self.assertEqual(first["outcome"], "timeout")
        self.assertIsNone(first["cost_usd"])
        self.assertNotEqual(first["event_id"], second["event_id"])
        self.assertEqual(second["cost_usd"], Decimal("0.2"))

    def test_observation_crosses_utc_day_without_rewriting_preparation_day(self):
        parent = receipt()
        local = datetime(2026, 10, 13, 3, 0, 1, tzinfo=timezone(timedelta(hours=3)))
        value = event(parent, observed_at=local)
        self.assertEqual(value["observed_at"], NOW + timedelta(seconds=2))
        self.assertEqual(parent["utc_day"].isoformat(), "2026-10-12")

    def test_invalid_identity_kind_time_and_state_are_rejected(self):
        for changes in (
            {"event_id": "A" * 32},
            {"event_kind": "overwrite"},
            {"observed_at": NOW.replace(tzinfo=None)},
            {"observed_at": NOW - timedelta(seconds=1)},
            {"dispatch_state": "possible"},
            {"outcome": "made_up"},
            {"dispatch_state": "not_dispatched"},
            {"outcome": "not_dispatched"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                event(**changes)

    def test_not_dispatched_is_not_free_and_cannot_claim_provider_evidence(self):
        value = event(dispatch_state="not_dispatched", outcome="not_dispatched")
        self.assertEqual(value["cost_certainty"], "not_dispatched")
        self.assertIsNone(value["cost_usd"])
        for envelope in (provider(0), usage(0, None)):
            with self.subTest(envelope=envelope), self.assertRaises(ValueError):
                event(envelope=envelope, dispatch_state="not_dispatched", outcome="not_dispatched")

    def test_possible_transport_outcomes_preserve_uncertainty(self):
        for outcome in ("timeout", "cancelled", "failed_transport", "unknown"):
            with self.subTest(outcome=outcome):
                value = event(dispatch_state="possible", outcome=outcome)
                self.assertEqual(value["outcome"], outcome)
                self.assertEqual(value["cost_certainty"], "undefined")

    def test_validate_accepts_normalized_values_without_mutation(self):
        for envelope in (None, provider(0), provider("0.1"), provider(None), usage(3, 5)):
            with self.subTest(envelope=envelope):
                value = event(envelope=envelope)
                snapshot = dict(value)
                self.assertEqual(EVENTS.validate_outcome_event(value, receipt()), value)
                self.assertEqual(value, snapshot)

    def test_validate_rejects_fabricated_evidence_and_wrong_field_set(self):
        original = event(envelope=provider("0.1"))
        for field, value in (
            ("tenant_id", 8),
            ("dispatch_id", "d" * 32),
            ("pricing_policy_version", "new"),
            ("cost_usd", Decimal(0)),
            ("cost_usd", 0.1),
            ("provider_cost_raw", "0.2"),
            ("price_source", "none"),
            ("cost_certainty", "free"),
            ("prompt_tokens", True),
            ("secret", "never stored"),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                EVENTS.validate_outcome_event(dict(original, **{field: value}), receipt())


class TestBase(DeclarativeBase):
    metadata = MetaData(schema="outcomes_review")
    __allow_unmapped__ = True


Table("tenants", TestBase.metadata, Column("id", Integer, primary_key=True))
DEPS = {
    "app.core.config": SimpleNamespace(settings=SimpleNamespace(DB_SCHEMA="outcomes_review")),
    "app.core.decorators": SimpleNamespace(app_label=lambda name: lambda cls: cls),
    "app.models.base": SimpleNamespace(Base=TestBase, TenantScopedMixin=type("Scoped", (), {})),
}
PARENT = load_isolated_source(
    "app.models.llm._outcome_parent", ROOT / "app/models/llm/llm_dispatch_receipt.py", imports=DEPS
)
LEAF = load_isolated_source("app.models.llm._outcome_model", ROOT / "app/models/llm/llm_outcome_event.py", imports=DEPS)


def capture_cache_tariff_additions(schema="outcomes_review"):
    captured = SimpleNamespace(columns={}, checks={}, dropped={})
    operations = SimpleNamespace(
        add_column=lambda table, column, **kwargs: captured.columns.setdefault(table, []).append(column),
        create_check_constraint=lambda name, table, condition, **kwargs: captured.checks.setdefault(table, {}).update(
            {name: str(condition)}
        ),
        drop_constraint=lambda name, table, **kwargs: captured.dropped.setdefault(table, set()).add(name),
        alter_column=lambda *args, **kwargs: None,
        execute=lambda *args, **kwargs: None,
        get_bind=lambda: SimpleNamespace(dialect=postgresql.dialect()),
    )
    migration = load_isolated_source(
        "_cache_tariff_outcome_migration",
        ROOT / "scripts/migrations/legacy_definitions/0107_add_cache_tariff_evidence.py",
        imports={
            "alembic": SimpleNamespace(op=operations),
            "app.core.config": SimpleNamespace(settings=SimpleNamespace(DB_SCHEMA=schema)),
        },
    )
    migration.upgrade()
    return migration, captured


class BaseManagerDouble:
    def __init__(self, model):
        self.model = model
        self.inserted_id = 1
        self.parent = receipt()
        self.stored = event()
        self.commit_error = None
        self.commits = self.rollbacks = 0
        self.guards = list(MANAGER.GUARDS)
        self.execute = AsyncMock(side_effect=self._execute)

    async def _execute(self, statement, params=None):
        if not getattr(statement, "is_select", False) and not getattr(statement, "is_insert", False):
            return SimpleNamespace(all=lambda: self.guards)
        if statement.is_insert:
            return SimpleNamespace(scalar_one_or_none=lambda: self.inserted_id)
        value = self.parent if "llm_dispatch_receipts" in str(statement) else self.stored
        return SimpleNamespace(mappings=lambda: SimpleNamespace(one_or_none=lambda: value))

    @asynccontextmanager
    async def _get_owned_session(self):
        try:
            yield SimpleNamespace(execute=self.execute)
            if self.commit_error:
                raise self.commit_error
            self.commits += 1
        except Exception:
            self.rollbacks += 1
            raise


MANAGER = load_isolated_source(
    "app.models.managers._outcome_storage",
    ROOT / "app/models/managers/llm_outcome_event_manager.py",
    imports={
        "app.core.tenant_context": TENANT,
        "app.services.ai.dispatch_receipts": RECEIPTS,
        "app.services.ai.outcome_events": EVENTS,
        "app.models.managers.base_manager": SimpleNamespace(BaseManager=BaseManagerDouble),
        "app.models.analysis.llm_dispatch_receipt": PARENT,
        "app.models.analysis.llm_outcome_event": LEAF,
    },
)


class StorageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        SCOPE["tenant_id"] = 7

    async def test_insert_acknowledges_owned_commit_with_named_conflict_only(self):
        manager = MANAGER.LLMOutcomeEventManager()
        self.assertTrue(await manager.record_event(event()))
        self.assertEqual(manager.commits, 1)
        sql = str(manager.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
        self.assertIn("ON CONFLICT ON CONSTRAINT uq_llm_outcome_events_identity DO NOTHING", sql)
        self.assertNotIn("DO UPDATE", sql)

    async def test_exact_replay_is_false_after_owned_commit(self):
        manager = MANAGER.LLMOutcomeEventManager()
        manager.inserted_id = None
        self.assertFalse(await manager.record_event(event()))
        self.assertEqual(manager.commits, 1)

    async def test_different_observation_is_conflict_not_overwrite(self):
        manager = MANAGER.LLMOutcomeEventManager()
        manager.inserted_id = None
        manager.stored = event(envelope=provider("0.1"))
        with self.assertRaises(MANAGER.OutcomeEvidenceConflict):
            await manager.record_event(event())
        self.assertEqual(manager.commits, 0)
        self.assertEqual(manager.rollbacks, 1)

    async def test_inaccessible_event_key_collision_fails_closed(self):
        manager = MANAGER.LLMOutcomeEventManager()
        manager.inserted_id = None
        manager.stored = None
        with self.assertRaises(MANAGER.OutcomeEvidenceConflict):
            await manager.record_event(event())
        params = manager.execute.call_args.args[0].compile().params
        self.assertEqual(params["tenant_id_1"], 7)

    async def test_wrong_or_missing_scope_denies_before_sql(self):
        for scope in (None, True, 8, 0, 2147483648):
            with self.subTest(scope=scope):
                SCOPE["tenant_id"] = scope
                manager = MANAGER.LLMOutcomeEventManager()
                with self.assertRaises(TenantError):
                    await manager.record_event(event())
                manager.execute.assert_not_awaited()

    async def test_missing_parent_denies_before_insert_and_parent_query_is_scoped(self):
        manager = MANAGER.LLMOutcomeEventManager()
        manager.parent = None
        with self.assertRaises(TenantError):
            await manager.record_event(event())
        self.assertEqual(manager.execute.await_count, 2)
        self.assertEqual(manager.execute.call_args.args[0].compile().params["tenant_id_1"], 7)

    async def test_persisted_parent_not_caller_basis_controls_validation(self):
        manager = MANAGER.LLMOutcomeEventManager()
        manager.parent = receipt(0, 0)
        with self.assertRaises(ValueError):
            await manager.record_event(event(envelope=usage(100, 100)))
        self.assertEqual(manager.execute.await_count, 2)

    async def test_each_required_guard_is_mandatory(self):
        for missing in MANAGER.GUARDS:
            with self.subTest(missing=missing):
                manager = MANAGER.LLMOutcomeEventManager()
                manager.guards.remove(missing)
                with self.assertRaises(MANAGER.OutcomeStorageUnavailable):
                    await manager.record_event(event())
                self.assertEqual(manager.execute.await_count, 1)
                self.assertEqual(manager.rollbacks, 1)

    async def test_guard_query_is_schema_function_and_replication_role_bound(self):
        manager = MANAGER.LLMOutcomeEventManager()
        await manager.record_event(event())
        query = manager.execute.call_args_list[0]
        self.assertEqual(query.args[1], {"schema": "outcomes_review"})
        for predicate in (
            "pn.nspname=:schema",
            "t.tgenabled IN ('O', 'A')",
            "t.tgqual IS NULL",
            "t.tgnargs=0",
            "t.tgattr::text=''",
            "session_replication_role",
        ):
            self.assertIn(predicate, str(query.args[0]))

    async def test_unrelated_integrity_error_propagates_with_rollback(self):
        manager = MANAGER.LLMOutcomeEventManager()

        async def execute(statement, params=None):
            if getattr(statement, "is_insert", False):
                raise IntegrityError("insert", {}, RuntimeError("other constraint"))
            return await manager._execute(statement, params)

        manager.execute.side_effect = execute
        with self.assertRaises(IntegrityError):
            await manager.record_event(event())
        self.assertEqual(manager.rollbacks, 1)

    async def test_commit_ack_loss_cannot_be_reported_as_stored(self):
        manager = MANAGER.LLMOutcomeEventManager()
        manager.commit_error = RuntimeError("ack lost")
        with self.assertRaisesRegex(RuntimeError, "ack lost"):
            await manager.record_event(event())
        self.assertEqual(manager.commits, 0)

    async def test_read_error_propagates_without_an_insert(self):
        manager = MANAGER.LLMOutcomeEventManager()
        manager.execute.side_effect = RuntimeError("read unavailable")
        with self.assertRaisesRegex(RuntimeError, "read unavailable"):
            await manager.record_event(event())
        self.assertEqual(manager.execute.await_count, 1)
        self.assertEqual(manager.rollbacks, 1)

    async def test_insert_error_propagates_without_acknowledgement(self):
        manager = MANAGER.LLMOutcomeEventManager()

        async def execute(statement, params=None):
            if getattr(statement, "is_insert", False):
                raise RuntimeError("write unavailable")
            return await manager._execute(statement, params)

        manager.execute.side_effect = execute
        with self.assertRaisesRegex(RuntimeError, "write unavailable"):
            await manager.record_event(event())
        self.assertEqual(manager.commits, 0)
        self.assertEqual(manager.rollbacks, 1)


class DefinitionTests(unittest.TestCase):
    def capture(self, schema="outcomes_review"):
        captured = SimpleNamespace(table=None, statements=[], indexes=[])

        def create_table(name, *items, **kwargs):
            captured.table = Table(name, MetaData(), *items, **kwargs)

        ops = SimpleNamespace(
            create_table=create_table,
            create_index=lambda *a, **k: captured.indexes.append((a, k)),
            get_bind=lambda: SimpleNamespace(dialect=postgresql.dialect()),
            execute=lambda sql: captured.statements.append(sql),
        )
        migration = load_isolated_source(
            "_outcome_migration",
            ROOT / "scripts/migrations/legacy_definitions/0094_add_llm_outcome_events.py",
            imports={
                "alembic": SimpleNamespace(op=ops),
                "app.core.config": SimpleNamespace(settings=SimpleNamespace(DB_SCHEMA=schema)),
            },
        )
        migration.upgrade()
        return migration, captured

    def test_columns_and_constraints_match_captured_definition(self):
        migration, captured = self.capture()
        _, cache_additions = capture_cache_tariff_additions()
        declared, defined = LEAF.LLMOutcomeEvent.__table__, captured.table
        defined_columns = {column.name: column for column in defined.columns}
        defined_columns.update({c.name: c for c in cache_additions.columns["llm_outcome_events"]})
        self.assertEqual(set(declared.c.keys()), set(defined_columns))
        for name in declared.c.keys():
            with self.subTest(name=name):
                actual, expected = declared.c[name], defined_columns[name]
                self.assertEqual(str(actual.type), str(expected.type))
                self.assertEqual(actual.nullable, expected.nullable)
                self.assertEqual(actual.primary_key, expected.primary_key)
                self.assertEqual(
                    str(actual.server_default.arg) if actual.server_default else None,
                    str(expected.server_default.arg) if expected.server_default else None,
                )
        for cls in (CheckConstraint, UniqueConstraint):

            def signature(table):
                return {
                    c.name: str(c.sqltext) if isinstance(c, CheckConstraint) else tuple(c.columns.keys())
                    for c in table.constraints
                    if isinstance(c, cls)
                }

            self.assertEqual(signature(declared), signature(defined))
        self.assertEqual(migration.down_revision, "0093")
        self.assertEqual(len([c for c in defined.constraints if isinstance(c, CheckConstraint)]), 11)

    def test_foreign_keys_restrict_parent_receipt_and_payer_deletion(self):
        _, captured = self.capture()
        for table in (LEAF.LLMOutcomeEvent.__table__, captured.table):
            self.assertEqual(
                {(fk.target_fullname, fk.ondelete) for fk in table.foreign_keys},
                {
                    ("outcomes_review.tenants.id", "RESTRICT"),
                    ("outcomes_review.llm_dispatch_receipts.dispatch_id", "RESTRICT"),
                },
            )

    def test_index_matches_definition_and_event_key_is_globally_unique(self):
        _, captured = self.capture()
        self.assertEqual(
            captured.indexes,
            [
                (
                    (
                        "ix_llm_outcome_events_tenant_receipt",
                        "llm_outcome_events",
                        ["tenant_id", "dispatch_id", "observed_at"],
                    ),
                    {"schema": "outcomes_review"},
                )
            ],
        )
        index = next(iter(LEAF.LLMOutcomeEvent.__table__.indexes))
        self.assertEqual(tuple(index.columns.keys()), ("tenant_id", "dispatch_id", "observed_at"))

    def test_guard_sql_is_quoted_and_binding_checks_payer_policy_and_time(self):
        _, captured = self.capture('review"schema')
        self.assertEqual(len(captured.statements), 5)
        for sql in captured.statements:
            self.assertIn('"review""schema".', sql)
        combined = "\n".join(captured.statements)
        for phrase in (
            "BEFORE UPDATE OR DELETE",
            "BEFORE TRUNCATE",
            "BEFORE INSERT",
            "ERRCODE = '55000'",
            "receipt.tenant_id IS DISTINCT FROM NEW.tenant_id",
            "receipt.pricing_policy_version IS DISTINCT FROM NEW.pricing_policy_version",
            "NEW.observed_at < receipt.prepared_at",
            "NEW.provider_cost_raw::numeric",
        ):
            self.assertIn(phrase, combined)

    def test_free_constraint_requires_nonnull_zero(self):
        checks = {
            c.name: str(c.sqltext) for c in LEAF.LLMOutcomeEvent.__table__.constraints if isinstance(c, CheckConstraint)
        }
        self.assertIn(
            "cost_certainty = 'free' AND cost_usd IS NOT NULL AND cost_usd = 0",
            checks["ck_llm_outcome_events_certainty"],
        )

    def test_downgrade_refuses_without_any_definition_operation(self):
        migration, captured = self.capture()
        before = list(captured.statements)
        with self.assertRaisesRegex(RuntimeError, "retained"):
            migration.downgrade()
        self.assertEqual(captured.statements, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
