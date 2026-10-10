"""Prepared manager transaction regressions; standalone, no application/DB imports.

Owner: python tests/test_learn_memory_batch.py (SQLAlchemy required).
Actual manager source is loaded with module-local infrastructure doubles.
These mocks do not prove PostgreSQL locking; see the separate DB cases.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import Column, Integer, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import declarative_base

sys.path.insert(0, str(Path(__file__).resolve().parent))
from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]
Base = declarative_base()


class Memory(Base):
    __tablename__ = "agent_memory"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer)
    scope = Column(String)
    key = Column(String)
    value = Column(String)
    source = Column(String)
    confidence = Column(Integer)
    evidence_message_id = Column(Integer)
    updated_at = Column(String)


class Message(Base):
    __tablename__ = "agent_messages"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer)
    role = Column(String)


class ManagerBase:
    def __class_getitem__(cls, model):
        return cls

    def __init__(self, model):
        self.model = model


class Session:
    def __init__(self, watermark="10", evidence=(11,)):
        self.watermark = watermark
        self.evidence = evidence
        self.execute = AsyncMock(side_effect=self.result)
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def result(self, statement):
        return SimpleNamespace(
            scalar_one=lambda: self.watermark,
            scalars=lambda: SimpleNamespace(all=lambda: list(self.evidence)),
        )


class BatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = Session()
        self.factory_calls = 0
        self.tenant = 7

        def factory():
            self.factory_calls += 1
            return self.session

        self.module = load_isolated_source(
            "app.models.managers._learn_batch_isolated",
            ROOT / "app/models/managers/agent_memory_manager.py",
            {
                "app.core.database": SimpleNamespace(async_session_maker=factory),
                "app.core.tenant_context": SimpleNamespace(
                    current_tenant_id=lambda: self.tenant, TenantContextError=RuntimeError
                ),
                "app.models.managers.base_manager": SimpleNamespace(BaseManager=ManagerBase),
                "app.models.agent_memory": SimpleNamespace(AgentMemory=Memory),
                "app.models.agent_message": SimpleNamespace(AgentMessage=Message),
            },
        )
        self.manager = self.module.agent_memory
        self.batch = SimpleNamespace(
            facts=[SimpleNamespace(key="style", value="brief", confidence=0.8, evidence_id=11)]
        )

    async def apply(self, **kwargs):
        return await self.manager.apply_learn_batch(
            self.batch, expected_watermark=kwargs.get("expected", 10), new_watermark=kwargs.get("new", 12)
        )

    def statements(self):
        return [call.args[0] for call in self.session.execute.await_args_list]

    async def test_one_session_one_commit_tenant_predicates_and_upsert_provenance(self):
        self.assertTrue(await self.apply())
        self.assertEqual(self.factory_calls, 1)
        self.session.commit.assert_awaited_once()
        self.session.rollback.assert_not_awaited()
        compiled = [statement.compile(dialect=postgresql.dialect()) for statement in self.statements()]
        self.assertEqual(len(compiled), 5)
        self.assertIn("ON CONFLICT (tenant_id, scope, key) DO NOTHING", str(compiled[0]))
        self.assertIn("FOR UPDATE", str(compiled[1]))
        self.assertIn("agent_memory.tenant_id", str(compiled[1]))
        self.assertIn(7, compiled[1].params.values())
        self.assertIn("agent_messages.tenant_id", str(compiled[2]))
        self.assertIn("user", compiled[2].params.values())
        self.assertIn("ON CONFLICT (tenant_id, scope, key) DO UPDATE", str(compiled[3]))
        for value in (7, "global", "style", "brief", 11):
            self.assertIn(value, compiled[3].params.values())
        self.assertIn("evidence_message_id = excluded.evidence_message_id", str(compiled[3]))
        self.assertIn("updated_at = now()", str(compiled[3]))
        self.assertIn("agent_memory.tenant_id", str(compiled[4]))
        self.assertIn("12", compiled[4].params.values())

    async def test_stale_cursor_rolls_back_before_facts_or_commit(self):
        self.session.watermark = "12"
        self.assertFalse(await self.apply())
        self.assertEqual(len(self.statements()), 2)
        self.session.rollback.assert_awaited_once()
        self.session.commit.assert_not_awaited()

    async def test_first_insert_mismatch_also_rolls_back(self):
        self.session.watermark = "0"
        self.assertFalse(await self.apply())
        self.session.rollback.assert_awaited_once()
        self.session.commit.assert_not_awaited()

    async def test_missing_scope_including_bypass_without_id_fails_before_session(self):
        self.tenant = None
        with self.assertRaisesRegex(RuntimeError, "concrete tenant"):
            await self.apply()
        self.assertEqual(self.factory_calls, 0)

    async def test_nonadvancing_or_invalid_cursor_fails_before_session(self):
        for expected, new in ((10, 10), (10, 9), (-1, 1), (True, 12), (10, "12")):
            with self.subTest(expected=expected, new=new):
                with self.assertRaises(ValueError):
                    await self.apply(expected=expected, new=new)
        self.assertEqual(self.factory_calls, 0)

    async def test_foreign_or_deleted_evidence_rolls_back_entire_transaction(self):
        self.session.evidence = ()
        with self.assertRaisesRegex(ValueError, "owned user message"):
            await self.apply()
        self.assertEqual(len(self.statements()), 3)
        self.session.rollback.assert_awaited_once()
        self.session.commit.assert_not_awaited()

    async def test_empty_valid_batch_advances_cursor_without_fact_writes(self):
        self.batch.facts = []
        self.assertTrue(await self.apply())
        self.assertEqual(len(self.statements()), 3)
        self.session.commit.assert_awaited_once()

    async def test_second_fact_error_rolls_back_and_propagates(self):
        self.batch.facts *= 2
        original = self.session.result

        async def execute(statement):
            if self.session.execute.await_count == 5:
                raise RuntimeError("second fact failed")
            return await original(statement)

        self.session.execute.side_effect = execute
        with self.assertRaisesRegex(RuntimeError, "second fact failed"):
            await self.apply()
        self.session.rollback.assert_awaited_once()
        self.session.commit.assert_not_awaited()

    async def test_commit_acknowledgement_failure_is_not_stale_cursor(self):
        self.session.commit.side_effect = RuntimeError("lost acknowledgement")
        with self.assertRaisesRegex(RuntimeError, "lost acknowledgement"):
            await self.apply()
        self.session.rollback.assert_awaited_once()

    async def test_cancellation_rolls_back_and_propagates(self):
        self.session.execute.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.apply()
        self.session.rollback.assert_awaited_once()
        self.session.commit.assert_not_awaited()

    async def test_legacy_malformed_cursor_matches_read_fallback_zero(self):
        self.session.watermark = "invalid"
        self.assertTrue(await self.apply(expected=0))
        self.session.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
