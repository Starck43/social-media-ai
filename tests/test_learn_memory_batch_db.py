"""Prepared PostgreSQL transaction regressions against an EXISTING test schema.

Owner: python tests/test_learn_memory_batch_db.py
No conftest/bootstrap, schema creation, seed, reset, migration or live model call.
Run sequentially with all other tests using the common test_schema. Only rows
owned by fresh test tenants are created/removed. No authoring run is claimed.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.setup_test_db import resolve_and_redirect

# Must precede application imports; refuses working-schema/database identity.
resolve_and_redirect()

from app.core.database import async_engine, async_session_maker
from app.core.tenant_context import TenantContextError, tenant_scope
from app.models import AgentMemory, AgentMessage, AgentSession, Tenant
from app.models.managers import agent_memory_manager as memory_module
from app.services.ai.output_contracts import learned_facts

manager = memory_module.agent_memory


class MemoryDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenants = []
        self.addAsyncCleanup(self.cleanup_rows)
        for _ in range(2):
            self.tenants.append(
                await Tenant.objects.create(slug=f"learn-batch-{uuid4().hex}", name="Learn batch test", plan="business")
            )
        self.own, self.other = self.tenants
        with tenant_scope(self.own.id):
            session = await AgentSession.objects.create(
                channel="telegram", chat_id=f"lb-{uuid4().hex}", kind="private"
            )
            self.message = await AgentMessage.objects.create(
                session_id=session.id, role="user", content="Brief reports"
            )

    async def cleanup_rows(self):
        try:
            for tenant in reversed(self.tenants):
                await Tenant.objects.delete_by_id(tenant.id)
        finally:
            await async_engine.dispose()  # unittest uses a separate loop for each case.

    def batch(self, key="style", value="brief", evidence_id=None):
        evidence_id = evidence_id if evidence_id is not None else self.message.id
        return learned_facts(
            {"facts": [{"key": key, "value": value, "confidence": 0.8, "evidence_id": evidence_id}]},
            {evidence_id},
        )

    async def apply(self, batch=None, expected=0):
        with tenant_scope(self.own.id):
            return await manager.apply_learn_batch(
                self.batch() if batch is None else batch,
                expected_watermark=expected,
                new_watermark=self.message.id,
            )

    async def rows(self):
        with tenant_scope(self.own.id):
            return list(await AgentMemory.objects.filter())

    async def test_fact_and_watermark_commit_with_provenance_only_for_owned_tenant(self):
        with tenant_scope(self.other.id):
            await manager.write("style", "foreign")
            await manager.write("learn_msg_wm", "777", scope="meta")
        self.assertTrue(await self.apply())
        rows = {row.key: row for row in await self.rows()}
        self.assertEqual(rows["learn_msg_wm"].value, str(self.message.id))
        self.assertEqual(rows["style"].evidence_message_id, self.message.id)
        self.assertEqual(rows["style"].source, "learn")
        self.assertAlmostEqual(rows["style"].confidence, 0.8)
        with tenant_scope(self.other.id):
            self.assertEqual(await manager.read("style"), "foreign")
            self.assertEqual(await manager.read("learn_msg_wm", scope="meta"), "777")

    async def test_existing_fact_is_upserted_without_unique_failure(self):
        with tenant_scope(self.own.id):
            await manager.write("style", "old", source="manual", confidence=0.3)
        self.assertTrue(await self.apply())
        facts = [row for row in await self.rows() if row.scope == "global"]
        self.assertEqual(len(facts), 1)
        self.assertEqual((facts[0].value, facts[0].source), ("brief", "learn"))

    async def test_stale_batch_neither_overwrites_facts_nor_rewinds_cursor(self):
        self.assertTrue(await self.apply())
        self.assertFalse(await self.apply(self.batch(value="stale")))
        with tenant_scope(self.own.id):
            self.assertEqual(await manager.read("style"), "brief")
            self.assertEqual(await manager.read("learn_msg_wm", scope="meta"), str(self.message.id))

    async def test_missing_meta_and_wrong_expected_cursor_does_not_create_meta(self):
        # A missing meta row reads as zero, not this deliberately nonzero expectation.
        with tenant_scope(self.own.id):
            applied = await manager.apply_learn_batch(
                self.batch(), expected_watermark=self.message.id, new_watermark=self.message.id + 1
            )
        self.assertFalse(applied)
        self.assertEqual(await self.rows(), [])

    async def test_empty_batch_commits_only_watermark(self):
        self.assertTrue(await self.apply(learned_facts({"facts": []}, set())))
        rows = await self.rows()
        self.assertEqual(
            [(row.scope, row.key, row.value) for row in rows], [("meta", "learn_msg_wm", str(self.message.id))]
        )

    async def test_foreign_evidence_rejected_without_facts_or_meta(self):
        with tenant_scope(self.other.id):
            session = await AgentSession.objects.create(
                channel="telegram", chat_id=f"lb-{uuid4().hex}", kind="private"
            )
            foreign = await AgentMessage.objects.create(session_id=session.id, role="user", content="Foreign")
        with self.assertRaisesRegex(ValueError, "owned user message"):
            await self.apply(self.batch(evidence_id=foreign.id))
        self.assertEqual(await self.rows(), [])

    async def test_missing_scope_or_bypass_without_tenant_is_rejected(self):
        for bypass in (False, True):
            with tenant_scope(bypass=bypass):
                with self.assertRaises(TenantContextError):
                    await manager.apply_learn_batch(self.batch(), expected_watermark=0, new_watermark=self.message.id)
        self.assertEqual(await self.rows(), [])

    async def assert_second_fact_rollback(self, existing_meta):
        with tenant_scope(self.own.id):
            await manager.write("style", "old", source="manual")
            if existing_meta:
                await manager.write("learn_msg_wm", "0", scope="meta")
        batch = learned_facts(
            {"facts": [fact.model_dump() for fact in self.batch().facts + self.batch(key="second").facts]},
            {self.message.id},
        )

        def factory():
            session = async_session_maker()
            original = session.execute
            calls = 0

            async def execute(statement, *args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 5:  # After actual first fact UPSERT, before cursor UPDATE.
                    raise RuntimeError("injected second fact failure")
                return await original(statement, *args, **kwargs)

            session.execute = execute
            return session

        with patch.object(memory_module, "async_session_maker", factory):
            with self.assertRaisesRegex(RuntimeError, "injected second fact failure"):
                await self.apply(batch)
        with tenant_scope(self.own.id):
            self.assertEqual(await manager.read("style"), "old")
            self.assertIsNone(await manager.read("second"))
            self.assertEqual(await manager.read("learn_msg_wm", scope="meta"), "0" if existing_meta else None)

    async def test_partial_batch_failure_rolls_back_new_meta_and_first_fact(self):
        await self.assert_second_fact_rollback(existing_meta=False)

    async def test_partial_batch_failure_preserves_existing_meta_and_fact(self):
        await self.assert_second_fact_rollback(existing_meta=True)

    async def assert_concurrent_single_winner(self, existing_meta):
        if existing_meta:
            with tenant_scope(self.own.id):
                await manager.write("learn_msg_wm", "0", scope="meta")
        ready = asyncio.Event()
        participants = 0

        def factory():
            session = async_session_maker()
            original = session.execute
            first = True

            async def execute(statement, *args, **kwargs):
                nonlocal first, participants
                if first:
                    first = False
                    participants += 1
                    if participants == 2:
                        ready.set()
                    await asyncio.wait_for(ready.wait(), timeout=10)
                return await original(statement, *args, **kwargs)

            session.execute = execute
            return session

        with patch.object(memory_module, "async_session_maker", factory):
            result = await asyncio.wait_for(
                asyncio.gather(self.apply(self.batch(value="one")), self.apply(self.batch(value="two"))), timeout=20
            )
        self.assertEqual(sorted(result), [False, True])
        with tenant_scope(self.own.id):
            self.assertEqual(await manager.read("style"), "one" if result[0] else "two")
            self.assertEqual(await manager.read("learn_msg_wm", scope="meta"), str(self.message.id))
        self.assertEqual(len(await self.rows()), 2)

    async def test_concurrent_first_learners_have_exactly_one_committed_batch(self):
        await self.assert_concurrent_single_winner(existing_meta=False)

    async def test_concurrent_existing_cursor_has_exactly_one_committed_batch(self):
        await self.assert_concurrent_single_winner(existing_meta=True)


if __name__ == "__main__":
    unittest.main()
