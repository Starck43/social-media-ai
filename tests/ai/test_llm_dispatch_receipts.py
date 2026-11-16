"""Receipt storage definitions and owned-session doubles; no DB or providers.

Run from repository root: .venv/bin/python tests/test_llm_dispatch_receipts.py
DDL definitions are captured, never applied. Fake sessions are not PG proof.
The registration subprocess blocks network and uses dummy settings only.
"""

import os
import subprocess
import sys
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from source_import_isolation import load_isolated_source
from sqlalchemy import Column, Integer, MetaData, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase

ROOT = Path(__file__).resolve().parents[2]
SCOPE = {"tenant_id": 7}
CACHE_PRICING = load_isolated_source("_receipt_cache_pricing", ROOT / "app/services/ai/cache_pricing.py")


class TenantError(RuntimeError):
    pass


TENANT = SimpleNamespace(TenantContextError=TenantError, current_tenant_id=lambda: SCOPE["tenant_id"])
JOURNAL = load_isolated_source(
    "_receipt_pricing",
    ROOT / "app/services/ai/attempt_journal.py",
    imports={"app.services.ai.cache_pricing": CACHE_PRICING},
)
HELPERS = load_isolated_source(
    "app.services.ai._receipt_helpers",
    ROOT / "app/services/ai/dispatch_receipts.py",
    imports={
        "app.services.ai.attempt_journal": JOURNAL,
        "app.services.ai.cache_pricing": CACHE_PRICING,
        "app.core.tenant_context": TENANT,
    },
)
NOW = datetime(2026, 10, 12, 23, 59, 59, tzinfo=timezone.utc)


def model(**changes):
    value = SimpleNamespace(
        id=4,
        model_id="model",
        provider=SimpleNamespace(id=2, name="provider"),
        input_cost_per_1k=0.001,
        output_cost_per_1k=0.002,
    )
    for key, item in changes.items():
        setattr(value, key, item)
    return value


def receipt(**changes):
    value = HELPERS.build_dispatch_receipt(
        model(), tenant_id=7, purpose="analysis", stage="text_analysis", dispatch_id="a" * 32, prepared_at=NOW
    )
    value.update(changes)
    return value


class ReceiptBuilderTests(unittest.TestCase):
    def test_exact_tariff_basis_is_frozen_not_repriced(self):
        selected = model()
        value = HELPERS.build_dispatch_receipt(
            selected, tenant_id=7, purpose="analysis", stage="text", dispatch_id="b" * 32, prepared_at=NOW
        )
        selected.input_cost_per_1k = 10
        self.assertEqual(value["input_cost_per_1k"], Decimal("0.001"))
        self.assertEqual(value["tariff_state"], "known")

    def test_explicit_zero_rates_are_free_basis_not_actual_provider_price(self):
        value = HELPERS.build_dispatch_receipt(
            model(
                input_cost_per_1k=0,
                output_cost_per_1k=0,
                cache_read_cost_per_1m=0,
                cache_write_5m_cost_per_1m=0,
                cache_write_1h_cost_per_1m=0,
            ),
            tenant_id=7,
            purpose="analysis",
            stage="text",
            dispatch_id="b" * 32,
            prepared_at=NOW,
        )
        self.assertEqual(value["tariff_state"], "free")
        self.assertNotIn("cost_usd", value)
        self.assertNotIn("reserved_usd", value)

    def test_missing_invalid_negative_and_unrepresentable_rates_stay_undefined(self):
        for rate in (None, True, -1, "NaN", "Infinity", "0.000000001", "10000000000"):
            with self.subTest(rate=rate):
                value = HELPERS.build_dispatch_receipt(
                    model(input_cost_per_1k=rate, output_cost_per_1k=0),
                    tenant_id=7,
                    purpose="analysis",
                    stage="text",
                    dispatch_id="b" * 32,
                    prepared_at=NOW,
                )
                self.assertIsNone(value["input_cost_per_1k"])
                self.assertEqual(value["tariff_state"], "undefined")
                self.assertEqual(value["output_cost_per_1k"], Decimal(0))

    def test_preparation_day_is_utc_not_caller_local_date(self):
        local = datetime(2026, 10, 13, 2, 30, tzinfo=timezone(timedelta(hours=3)))
        value = HELPERS.build_dispatch_receipt(
            model(), tenant_id=7, purpose="analysis", stage="text", dispatch_id="b" * 32, prepared_at=local
        )
        self.assertEqual(value["utc_day"].isoformat(), "2026-10-12")
        self.assertEqual(value["prepared_at"].tzinfo, timezone.utc)

    def test_naive_preparation_time_is_rejected(self):
        with self.assertRaises(ValueError):
            HELPERS.validate_receipt(receipt(prepared_at=NOW.replace(tzinfo=None)))

    def test_wrong_utc_day_is_rejected(self):
        with self.assertRaises(ValueError):
            HELPERS.validate_receipt(receipt(utc_day=NOW.date() + timedelta(days=1)))

    def test_invalid_dispatch_identity_is_rejected(self):
        for identity in (None, "", "A" * 32, "a" * 31, "a" * 33, 1):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                HELPERS.validate_receipt(receipt(dispatch_id=identity))

    def test_payer_and_model_provider_ids_must_be_positive_storage_integers(self):
        for field in ("tenant_id", "llm_model_row_id", "llm_provider_row_id"):
            for item in (None, True, 0, -1, 2147483648):
                with self.subTest(field=field, item=item), self.assertRaises(ValueError):
                    HELPERS.validate_receipt(receipt(**{field: item}))

    def test_execution_refs_are_validated_without_cascade_relations(self):
        for field in HELPERS.EXECUTION_FIELDS:
            with self.subTest(field=field):
                self.assertEqual(HELPERS.validate_receipt(receipt(**{field: 99}))[field], 99)
                with self.assertRaises(ValueError):
                    HELPERS.validate_receipt(receipt(**{field: -1}))

    def test_unknown_fields_or_missing_fields_are_rejected(self):
        for field in ("prompt", "api_key", "id", "created_at", "cost_usd"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                HELPERS.validate_receipt(receipt(**{field: "PRIVATE"}))
        value = receipt()
        del value["model_id"]
        with self.assertRaises(ValueError):
            HELPERS.validate_receipt(value)

    def test_labels_and_names_are_bounded(self):
        for field, value in (
            ("purpose", "caller text"),
            ("stage", "A"),
            ("model_id", ""),
            ("provider_name", "x" * 201),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                HELPERS.validate_receipt(receipt(**{field: value}))

    def test_inconsistent_tariff_state_or_policy_is_rejected(self):
        for changes in ({"tariff_state": "free"}, {"pricing_policy_version": "injected"}, {"input_cost_per_1k": 0.1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                HELPERS.validate_receipt(receipt(**changes))

    def test_execution_payload_cannot_supply_payer_or_prompt(self):
        for field in ("tenant_id", "dispatch_id", "prompt"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                HELPERS.build_dispatch_receipt(
                    model(),
                    tenant_id=7,
                    purpose="analysis",
                    stage="text",
                    dispatch_id="b" * 32,
                    prepared_at=NOW,
                    execution={field: "PRIVATE"},
                )

    def test_input_record_is_not_mutated(self):
        value = receipt()
        snapshot = dict(value)
        self.assertEqual(HELPERS.validate_receipt(value), snapshot)
        self.assertEqual(value, snapshot)


class PreparationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        SCOPE["tenant_id"] = 7

    async def test_payer_is_from_scope_and_result_is_immutable(self):
        store = SimpleNamespace(record_receipt=AsyncMock(return_value=True))
        value = await HELPERS.prepare_dispatch_receipt(model(), purpose="analysis", stage="text", journal=store)
        self.assertEqual(value["tenant_id"], 7)
        with self.assertRaises(TypeError):
            value["tenant_id"] = 8

    async def test_identical_preparations_get_distinct_raw_uuids(self):
        store = SimpleNamespace(record_receipt=AsyncMock(return_value=True))
        first = await HELPERS.prepare_dispatch_receipt(model(), purpose="analysis", stage="text", journal=store)
        second = await HELPERS.prepare_dispatch_receipt(model(), purpose="analysis", stage="text", journal=store)
        self.assertNotEqual(first["dispatch_id"], second["dispatch_id"])
        self.assertEqual(len(first["dispatch_id"]), 32)

    async def test_missing_scope_never_bootstraps_an_operator_payer(self):
        SCOPE["tenant_id"] = None
        store = SimpleNamespace(record_receipt=AsyncMock())
        with self.assertRaises(TenantError):
            await HELPERS.prepare_dispatch_receipt(model(), purpose="analysis", stage="text", journal=store)
        store.record_receipt.assert_not_awaited()

    async def test_fresh_identity_collision_is_not_acknowledged_as_a_new_preparation(self):
        store = SimpleNamespace(record_receipt=AsyncMock(return_value=False))
        with self.assertRaisesRegex(RuntimeError, "already stored"):
            await HELPERS.prepare_dispatch_receipt(model(), purpose="analysis", stage="text", journal=store)

    async def test_lost_commit_ack_remains_an_error(self):
        store = SimpleNamespace(record_receipt=AsyncMock(side_effect=RuntimeError("ack lost")))
        with self.assertRaisesRegex(RuntimeError, "ack lost"):
            await HELPERS.prepare_dispatch_receipt(model(), purpose="analysis", stage="text", journal=store)


class TestBase(DeclarativeBase):
    metadata = MetaData(schema="receipts_review")
    __allow_unmapped__ = True


Table("tenants", TestBase.metadata, Column("id", Integer, primary_key=True))


def label(name):
    def decorate(cls):
        cls._app_label = name
        return cls

    return decorate


LEAF = load_isolated_source(
    "app.models.analysis._receipt_model",
    ROOT / "app/models/llm/llm_dispatch_receipt.py",
    imports={
        "app.core.config": SimpleNamespace(settings=SimpleNamespace(DB_SCHEMA="receipts_review")),
        "app.core.decorators": SimpleNamespace(app_label=label),
        "app.models.base": SimpleNamespace(
            Base=TestBase, TenantScopedMixin=type("Scoped", (), {"__tenant_scoped__": True})
        ),
    },
)


def capture_cache_tariff_additions(schema="receipts_review"):
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
        "_cache_tariff_receipt_migration",
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
        self.stored = receipt()
        self.commit_error = None
        self.commits = 0
        self.guards = [("llm_dispatch_receipts_immutable", 27), ("llm_dispatch_receipts_no_truncate", 34)]
        self.execute = AsyncMock(side_effect=self._execute)

    async def _execute(self, statement, params=None):
        if not getattr(statement, "is_select", False) and not getattr(statement, "is_insert", False):
            return SimpleNamespace(all=lambda: self.guards)
        if statement.is_insert:
            return SimpleNamespace(scalar_one_or_none=lambda: self.inserted_id)
        return SimpleNamespace(mappings=lambda: SimpleNamespace(one_or_none=lambda: self.stored))

    @asynccontextmanager
    async def _get_owned_session(self):
        yield SimpleNamespace(execute=self.execute)
        if self.commit_error:
            raise self.commit_error
        self.commits += 1


MANAGER = load_isolated_source(
    "app.models.managers._receipt_storage",
    ROOT / "app/models/managers/llm_dispatch_receipt_manager.py",
    imports={
        "app.core.tenant_context": TENANT,
        "app.services.ai.dispatch_receipts": HELPERS,
        "app.models.managers.base_manager": SimpleNamespace(BaseManager=BaseManagerDouble),
        "app.models.llm.llm_dispatch_receipt": LEAF,
    },
)


class ReceiptStorageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        SCOPE["tenant_id"] = 7

    async def test_insert_ack_requires_owned_commit_and_named_conflict_only(self):
        manager = MANAGER.LLMDispatchReceiptManager()
        self.assertTrue(await manager.record_receipt(receipt()))
        self.assertEqual(manager.commits, 1)
        sql = str(manager.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
        self.assertIn("ON CONFLICT ON CONSTRAINT uq_llm_dispatch_receipts_identity DO NOTHING", sql)
        self.assertNotIn("DO UPDATE", sql)

    async def test_exact_replay_returns_false(self):
        manager = MANAGER.LLMDispatchReceiptManager()
        manager.inserted_id = None
        self.assertFalse(await manager.record_receipt(receipt()))

    async def test_changed_snapshot_time_or_execution_is_conflict(self):
        for field, value in (
            ("input_cost_per_1k", Decimal("3")),
            ("prepared_at", NOW - timedelta(seconds=1)),
            ("job_id", 9),
        ):
            with self.subTest(field=field):
                manager = MANAGER.LLMDispatchReceiptManager()
                manager.inserted_id = None
                with self.assertRaises(MANAGER.ReceiptEvidenceConflict):
                    await manager.record_receipt(receipt(**{field: value}))
                self.assertEqual(manager.commits, 0)

    async def test_other_tenant_collision_is_not_success_and_read_is_scoped(self):
        manager = MANAGER.LLMDispatchReceiptManager()
        manager.inserted_id = None
        manager.stored = None
        with self.assertRaises(MANAGER.ReceiptEvidenceConflict):
            await manager.record_receipt(receipt())
        self.assertEqual(manager.execute.call_args.args[0].compile().params["tenant_id_1"], 7)

    async def test_unrelated_integrity_error_is_not_swallowed(self):
        manager = MANAGER.LLMDispatchReceiptManager()

        async def reject_insert(statement, params=None):
            if getattr(statement, "is_insert", False):
                raise IntegrityError("insert", {}, RuntimeError("other constraint"))
            return await manager._execute(statement, params)

        manager.execute.side_effect = reject_insert
        with self.assertRaises(IntegrityError):
            await manager.record_receipt(receipt())

    async def test_lost_commit_ack_is_not_a_receipt_ack(self):
        manager = MANAGER.LLMDispatchReceiptManager()
        manager.commit_error = RuntimeError("ack lost")
        with self.assertRaisesRegex(RuntimeError, "ack lost"):
            await manager.record_receipt(receipt())

    async def test_missing_or_mismatched_scope_is_denied_before_sql(self):
        for tenant_id in (None, 8, True):
            SCOPE["tenant_id"] = tenant_id
            manager = MANAGER.LLMDispatchReceiptManager()
            with self.assertRaises(TenantError):
                await manager.record_receipt(receipt())
            manager.execute.assert_not_awaited()

    async def test_wrong_field_set_is_denied_before_sql(self):
        manager = MANAGER.LLMDispatchReceiptManager()
        with self.assertRaises(ValueError):
            await manager.record_receipt(receipt(prompt="PRIVATE"))
        manager.execute.assert_not_awaited()

    async def test_missing_or_wrongly_typed_guards_block_before_insert(self):
        for guards in (
            [],
            [("llm_dispatch_receipts_immutable", 27)],
            [("llm_dispatch_receipts_immutable", 19), ("llm_dispatch_receipts_no_truncate", 34)],
        ):
            with self.subTest(guards=guards):
                manager = MANAGER.LLMDispatchReceiptManager()
                manager.guards = guards
                with self.assertRaises(MANAGER.ReceiptStorageUnavailable):
                    await manager.record_receipt(receipt())
                self.assertEqual(manager.execute.await_count, 1)
                self.assertEqual(manager.commits, 0)

    async def test_guard_query_is_schema_bound_and_requires_enabled_expected_function(self):
        manager = MANAGER.LLMDispatchReceiptManager()
        await manager.record_receipt(receipt())
        query = manager.execute.call_args_list[0].args[0]
        bound = manager.execute.call_args_list[0].args[1]
        self.assertEqual(bound, {"schema": "receipts_review", "table": "llm_dispatch_receipts"})
        self.assertIn("t.tgenabled IN ('O', 'A')", str(query))
        self.assertIn("p.proname = 'reject_llm_receipt_mutation'", str(query))


class DefinitionTests(unittest.TestCase):
    def capture(self, schema='review"schema'):
        captured = SimpleNamespace(table=None, kwargs=None, statements=[], indexes=[])

        def create_table(name, *items, **kwargs):
            captured.table = (name, items)
            captured.kwargs = kwargs

        operations = SimpleNamespace(
            create_table=create_table,
            create_index=lambda *args, **kwargs: captured.indexes.append((args, kwargs)),
            get_bind=lambda: SimpleNamespace(dialect=postgresql.dialect()),
            execute=lambda statement: captured.statements.append(statement),
        )
        migration = load_isolated_source(
            "_receipt_migration_definition",
            ROOT / "scripts/migrations/legacy_definitions/0093_add_llm_dispatch_receipts.py",
            imports={
                "alembic": SimpleNamespace(op=operations),
                "app.core.config": SimpleNamespace(settings=SimpleNamespace(DB_SCHEMA=schema)),
            },
        )
        migration.upgrade()
        return migration, captured

    def test_model_and_migration_columns_and_constraints_match(self):
        migration, captured = self.capture()
        columns = {item.name: item for item in captured.table[1] if isinstance(item, Column)}
        _, cache_additions = capture_cache_tariff_additions()
        columns.update({c.name: c for c in cache_additions.columns["llm_dispatch_receipts"]})
        self.assertEqual(set(columns), set(LEAF.LLMDispatchReceipt.__table__.columns.keys()))
        for name, column in columns.items():
            actual = LEAF.LLMDispatchReceipt.__table__.c[name]
            self.assertEqual(str(actual.type), str(column.type))
            self.assertEqual(actual.nullable, column.nullable)
        definitions = {item.name: str(item.sqltext) for item in captured.table[1] if hasattr(item, "sqltext")}
        for name in cache_additions.dropped.get("llm_dispatch_receipts", set()):
            definitions.pop(name, None)
        definitions.update(cache_additions.checks["llm_dispatch_receipts"])
        declared = {
            item.name: str(item.sqltext)
            for item in LEAF.LLMDispatchReceipt.__table__.constraints
            if hasattr(item, "sqltext")
        }
        self.assertEqual(definitions, declared)
        self.assertEqual(migration.down_revision, "0092")

    def test_only_tenant_fk_exists_and_delete_is_restricted(self):
        table = LEAF.LLMDispatchReceipt.__table__
        self.assertEqual(len(table.foreign_keys), 1)
        fk = next(iter(table.foreign_keys))
        self.assertEqual(fk.ondelete, "RESTRICT")
        self.assertTrue(fk.target_fullname.endswith(".tenants.id"))

    def test_trigger_definitions_are_schema_quoted_and_cover_mutation_and_truncate(self):
        _, captured = self.capture()
        self.assertEqual(captured.kwargs["schema"], 'review"schema')
        for statement in captured.statements:
            self.assertIn('"review""schema".', statement)
        sql = "\n".join(captured.statements)
        self.assertIn("BEFORE UPDATE OR DELETE", sql)
        self.assertIn("BEFORE TRUNCATE", sql)
        self.assertIn("ERRCODE = '55000'", sql)

    def test_downgrade_refuses_to_delete_evidence(self):
        migration, captured = self.capture()
        before = list(captured.statements)
        with self.assertRaisesRegex(RuntimeError, "retained"):
            migration.downgrade()
        self.assertEqual(captured.statements, before)

    def test_storage_has_no_cost_reservation_or_outcome_claim(self):
        columns = set(LEAF.LLMDispatchReceipt.__table__.columns.keys())
        self.assertFalse(columns & {"cost_usd", "reserved_usd", "outcome", "prompt", "api_key"})

    def test_fresh_actual_registry_imports_bind_once_without_network_or_sql(self):
        code = """
import socket
def deny(*args, **kwargs):
    raise AssertionError('Unexpected network connection')
socket.socket.connect = deny
from sqlalchemy.orm import configure_mappers
from app.models import Base, LLMDispatchReceipt
from app.models.llm import LLMDispatchReceipt as domain
from app.models.llm.llm_dispatch_receipt import LLMDispatchReceipt as public
from app.models.llm.llm_dispatch_receipt import LLMDispatchReceipt as leaf
assert LLMDispatchReceipt is domain is public is leaf
configure_mappers()
assert LLMDispatchReceipt.objects.model is LLMDispatchReceipt
assert sum(m.class_ is LLMDispatchReceipt for m in Base.registry.mappers) == 1
assert next(iter(LLMDispatchReceipt.__table__.c.tenant_id.foreign_keys)).ondelete == 'RESTRICT'
"""
        env = dict(
            os.environ,
            SECRET_KEY="synthetic-import-check",
            POSTGRES_URL="postgresql://dummy:dummy@localhost/dummy",
            DB_SCHEMA="receipts_review",
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
        # Failure detail is intentionally not emitted: local config can be private.
        self.assertEqual(result.returncode, 0, "Actual registry import failed; inspect the captured stderr locally")


if __name__ == "__main__":
    unittest.main(verbosity=2)
