"""Standalone verifier regression tests: no app config, DB, pytest or DDL."""

import asyncio
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]
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

    def test_digest_is_order_independent(self):
        self.assertEqual(MODULE.digest({"a": 1, "b": 2}), MODULE.digest({"b": 2, "a": 1}))

    def test_digest_detects_content_change(self):
        self.assertNotEqual(MODULE.digest({"a": 1}), MODULE.digest({"a": 2}))

    def test_digest_preserves_exact_decimal(self):
        from decimal import Decimal

        self.assertNotEqual(MODULE.digest(Decimal("9999999999.99999998")), MODULE.digest(Decimal("9999999999.99999999")))

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
            "app.models.analysis._verifier_model",
            ROOT / "app/models/analysis/llm_dispatch_receipt.py",
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
            ROOT / "migrations/versions/0093_add_llm_dispatch_receipts.py",
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