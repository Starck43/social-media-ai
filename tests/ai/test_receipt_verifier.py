"""Standalone verifier regression tests: no app config, DB, pytest or DDL."""

import asyncio
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]
MODULE = load_isolated_source("_receipt_verifier", ROOT / "scripts/verify_llm_dispatch_receipts.py")


class FakeSession:
    def __init__(self, events):
        self.events = events

    async def rollback(self):
        self.events.append("rollback")


class HelpersTests(unittest.TestCase):
    def test_require_blocks_with_safe_label(self):
        with self.assertRaisesRegex(MODULE.VerificationFailure, "fixed label"):
            MODULE.require(False, "fixed label")

    def test_require_success(self):
        self.assertIsNone(MODULE.require(True, "unused"))

    def test_catalog_relation_queries_explicitly_type_asyncpg_schema_bind(self):
        import ast
        import inspect

        from sqlalchemy import text
        from sqlalchemy.dialects.postgresql.asyncpg import dialect

        nodes = ast.walk(ast.parse(inspect.getsource(MODULE.catalog)))
        statements = [
            node.value
            for node in nodes
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "to_regclass(" in node.value
        ]
        self.assertEqual(len(statements), 2)
        for sql in statements:
            with self.subTest(query=sql):
                compiled = text(sql).compile(dialect=dialect())
                self.assertIn("format('%I.llm_dispatch_receipts', CAST($1 AS text))", str(compiled))
                self.assertEqual(compiled.positiontup, ["schema"])
                self.assertEqual(compiled.params, {"schema": None})

    def test_digest_is_order_independent(self):
        self.assertEqual(MODULE.digest({"a": 1, "b": 2}), MODULE.digest({"b": 2, "a": 1}))

    def test_digest_detects_content_change(self):
        self.assertNotEqual(MODULE.digest({"a": 1}), MODULE.digest({"a": 2}))

    def test_digest_preserves_exact_decimal(self):
        from decimal import Decimal

        self.assertNotEqual(
            MODULE.digest(Decimal("9999999999.99999998")), MODULE.digest(Decimal("9999999999.99999999"))
        )

    def test_signature_tracks_string_length(self):
        from sqlalchemy import Column, MetaData, String, Table

        first = Table("evidence", MetaData(), Column("identity", String(32)))
        second = Table("evidence", MetaData(), Column("identity", String(31)))
        self.assertNotEqual(MODULE.table_signature(first), MODULE.table_signature(second))

    def test_signature_tracks_nullable_and_default(self):
        from sqlalchemy import Column, Integer, MetaData, Table, text

        first = Table("evidence", MetaData(), Column("id", Integer, nullable=False))
        second = Table("evidence", MetaData(), Column("id", Integer, nullable=True, server_default=text("0")))
        self.assertNotEqual(MODULE.table_signature(first), MODULE.table_signature(second))

    def test_capture_is_unbound_and_matches_isolated_real_model(self):
        from sqlalchemy import MetaData
        from sqlalchemy.orm import DeclarativeBase

        class TestBase(DeclarativeBase):
            __allow_unmapped__ = True
            metadata = MetaData(schema="test_schema")

        settings = SimpleNamespace(DB_SCHEMA="test_schema")
        model = load_isolated_source(
            "app.models.llm._verifier_model",
            ROOT / "app/models/llm/llm_dispatch_receipt.py",
            imports={
                "app.core.config": SimpleNamespace(settings=settings),
                "app.core.decorators": SimpleNamespace(app_label=lambda label: lambda cls: cls),
                "app.models.base": SimpleNamespace(Base=TestBase, TenantScopedMixin=type("EmptyMixin", (), {})),
            },
        )
        # Load the real revision with module-local settings and capture operations
        # without ever creating a connection or importing real application config.
        revision = load_isolated_source(
            "_verifier_revision",
            ROOT / "scripts/migrations/legacy_definitions/0093_add_llm_dispatch_receipts.py",
            imports={"app.core.config": SimpleNamespace(settings=settings)},
        )
        from sqlalchemy import Index, Table
        from sqlalchemy.dialects import postgresql

        tables = []
        statements = []

        def create_table(name, *items, **kwargs):
            tables.append(Table(name, MetaData(), *items, **kwargs))

        def create_index(name, table, columns, **kwargs):
            Index(name, *(tables[0].c[column] for column in columns))

        revision.op = SimpleNamespace(
            create_table=create_table,
            create_index=create_index,
            get_bind=lambda: SimpleNamespace(dialect=postgresql.dialect()),
            execute=statements.append,
        )
        revision.upgrade()
        self.assertEqual(MODULE.table_signature(tables[0]), MODULE.table_signature(model.LLMDispatchReceipt.__table__))
        self.assertEqual(len(statements), 3)
        self.assertIn(MODULE.FUNCTION_BODY, statements[0])
        self.assertEqual(len(MODULE.CHECKS), 5)


class ReflectedKeyTests(unittest.TestCase):
    def setUp(self):
        from sqlalchemy import Column, Index, Integer, MetaData, String, Table, UniqueConstraint

        self.table = Table(
            "llm_dispatch_receipts",
            MetaData(),
            Column("dispatch_id", String(32)),
            Column("tenant_id", Integer),
            Column("utc_day", String(10)),
            UniqueConstraint("dispatch_id", name="uq_llm_dispatch_receipts_identity"),
            Index("ix_llm_dispatch_receipts_tenant_day", "tenant_id", "utc_day"),
            schema="test_schema",
        )
        self.uniques = [{"name": "uq_llm_dispatch_receipts_identity", "column_names": ["dispatch_id"]}]
        self.indexes = [
            {
                "name": "ix_llm_dispatch_receipts_tenant_day",
                "column_names": ["tenant_id", "utc_day"],
                "unique": False,
            }
        ]

    def verify(self):
        inspector = SimpleNamespace(
            get_unique_constraints=lambda name, schema: self.uniques,
            get_indexes=lambda name, schema: self.indexes,
        )
        return MODULE.reflected_keys(inspector, self.table)

    def test_unique_reported_only_as_constraint_passes(self):
        result = self.verify()
        self.assertEqual(result["unique_constraints"], self.uniques)
        self.assertEqual(result["indexes"], self.indexes)

    def test_explicit_matching_backing_index_also_passes(self):
        self.indexes.append(
            {
                "name": "uq_llm_dispatch_receipts_identity",
                "column_names": ["dispatch_id"],
                "unique": True,
                "duplicates_constraint": "uq_llm_dispatch_receipts_identity",
            }
        )
        self.assertEqual(len(self.verify()["indexes"]), 2)

    def test_missing_unique_constraint_fails(self):
        self.uniques.clear()
        with self.assertRaisesRegex(MODULE.VerificationFailure, "Unique constraint parity"):
            self.verify()

    def test_wrong_unique_columns_fail(self):
        self.uniques[0]["column_names"] = ["tenant_id", "dispatch_id"]
        with self.assertRaisesRegex(MODULE.VerificationFailure, "Unique constraint parity"):
            self.verify()

    def test_duplicate_unique_constraint_entries_fail(self):
        self.uniques.append(dict(self.uniques[0]))
        with self.assertRaisesRegex(MODULE.VerificationFailure, "Unique constraint parity"):
            self.verify()

    def test_standalone_unique_index_cannot_replace_constraint(self):
        self.uniques.clear()
        self.indexes.append(
            {"name": "uq_llm_dispatch_receipts_identity", "column_names": ["dispatch_id"], "unique": True}
        )
        with self.assertRaisesRegex(MODULE.VerificationFailure, "Unique constraint parity"):
            self.verify()

    def test_unexpected_standalone_index_fails(self):
        self.indexes.append({"name": "extra", "column_names": ["dispatch_id"], "unique": True})
        with self.assertRaisesRegex(MODULE.VerificationFailure, "Index parity"):
            self.verify()

    def test_bogus_duplicate_marker_fails(self):
        self.indexes[0]["duplicates_constraint"] = "missing"
        with self.assertRaisesRegex(MODULE.VerificationFailure, "Constraint backing index parity"):
            self.verify()

    def test_wrong_index_column_order_fails(self):
        self.indexes[0]["column_names"].reverse()
        with self.assertRaisesRegex(MODULE.VerificationFailure, "Index parity"):
            self.verify()

    def test_partial_index_fails(self):
        self.indexes[0]["dialect_options"] = {"postgresql_where": "tenant_id = 1"}
        with self.assertRaisesRegex(MODULE.VerificationFailure, "Partial index"):
            self.verify()

    def test_covering_index_fails(self):
        self.indexes[0]["include_columns"] = ["dispatch_id"]
        with self.assertRaisesRegex(MODULE.VerificationFailure, "Unexpected covering index"):
            self.verify()

    def test_wrong_index_method_fails(self):
        self.indexes[0]["dialect_options"] = {"postgresql_using": "hash"}
        with self.assertRaisesRegex(MODULE.VerificationFailure, "Index method"):
            self.verify()


class BoundaryTests(unittest.IsolatedAsyncioTestCase):
    def owned(self, events, fail_commit=False):
        @asynccontextmanager
        async def context():
            events.append("open")
            try:
                yield FakeSession(events)
            except BaseException:
                events.append("rollback")
                raise
            else:
                events.append("commit")
                if fail_commit:
                    raise ValueError("real commit failed")
            finally:
                events.append("close")

        return context

    def factory(self, events):
        @asynccontextmanager
        async def factory():
            events.append("open")
            try:
                yield FakeSession(events)
            finally:
                events.append("close")

        return factory

    async def test_ack_error_only_after_commit(self):
        events = []
        with self.assertRaises(MODULE.InjectedBoundaryFailure):
            async with MODULE.after_commit_fault(self.owned(events)):
                events.append("insert")
        self.assertEqual(events, ["open", "insert", "commit", "close"])

    async def test_ack_hook_does_not_replace_real_commit_error(self):
        events = []
        with self.assertRaisesRegex(ValueError, "real commit failed"):
            async with MODULE.after_commit_fault(self.owned(events, fail_commit=True)):
                pass
        self.assertEqual(events, ["open", "commit", "close"])

    async def test_precommit_hook_rolls_back(self):
        events = []
        with self.assertRaises(MODULE.InjectedBoundaryFailure):
            async with MODULE.before_commit_fault(self.owned(events)):
                events.append("insert")
        self.assertEqual(events, ["open", "insert", "rollback", "close"])

    async def test_guard_probe_normal_exit_never_commits(self):
        events = []
        async with MODULE.rollback_only(self.factory(events)):
            events.append("disable")
        self.assertEqual(events, ["open", "disable", "rollback", "close"])

    async def test_guard_probe_assertion_still_rolls_back(self):
        events = []
        with self.assertRaises(AssertionError):
            async with MODULE.rollback_only(self.factory(events)):
                raise AssertionError("probe failed")
        self.assertEqual(events, ["open", "rollback", "close"])

    async def test_guard_probe_cancellation_still_rolls_back(self):
        events = []
        with self.assertRaises(asyncio.CancelledError):
            async with MODULE.rollback_only(self.factory(events)):
                raise asyncio.CancelledError()
        self.assertEqual(events, ["open", "rollback", "close"])

    async def test_pair_drains_both_on_exception(self):
        events = []

        async def fails():
            raise ValueError("fixed failure")

        async def completes():
            await asyncio.sleep(0)
            events.append("finished")
            return False

        outcomes = await MODULE.drain_pair(fails(), completes())
        self.assertIsInstance(outcomes[0], ValueError)
        self.assertIs(outcomes[1], False)
        self.assertEqual(events, ["finished"])

    async def test_pair_timeout_cancels_and_drains_both(self):
        events = []

        async def waiting(label):
            try:
                await asyncio.Event().wait()
            finally:
                events.append(label)

        with self.assertRaises(TimeoutError):
            await MODULE.drain_pair(waiting("first"), waiting("second"), timeout=0.02)
        self.assertEqual(set(events), {"first", "second"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
