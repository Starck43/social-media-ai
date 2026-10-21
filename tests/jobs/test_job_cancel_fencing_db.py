"""Prepared cancellation concurrency checks; owner execution only.

Uses the existing reviewed test schema via the claim DB fixture. No conftest,
bootstrap/reset/migrations, provider or live calls. Run sequentially with other
DB/pytest processes. Only this test's generated tenants/rows are cleaned up.
"""

import asyncio
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

import test_job_claim_outcomes_db as db_fixtures
from sqlalchemy import event

from app.core import database as database_module
from app.core.database import async_session_maker
from app.core.tenant_context import TenantContextError, tenant_scope
from app.jobs.claim_outcomes import OutcomeAck


class CancelDatabaseTests(unittest.IsolatedAsyncioTestCase):
    # Reuse fixture helpers, not the fifteen outcome test methods.
    asyncSetUp = db_fixtures.ClaimDatabaseTests.asyncSetUp
    cleanup_rows = db_fixtures.ClaimDatabaseTests.cleanup_rows
    acquired = db_fixtures.ClaimDatabaseTests.acquired
    read = db_fixtures.ClaimDatabaseTests.read
    mutate = db_fixtures.ClaimDatabaseTests.mutate
    done = db_fixtures.ClaimDatabaseTests.done
    evidence = staticmethod(db_fixtures.ClaimDatabaseTests.evidence)

    async def cancel(self, claim):
        with tenant_scope(claim.tenant_id):
            return await self.manager.cancel_running(claim)

    async def test_cancel_preserves_audit_and_blocks_old_worker(self):
        job, claim = await self.acquired()
        await self.mutate(job.id, result={"audit": "keep"}, llm_cost=0.25)
        before = self.evidence(await self.read(job.id))
        self.assertTrue(await self.cancel(claim))
        row = await self.read(job.id)
        self.assertEqual((row.status, row.error), ("failed", "Cancelled by operator"))
        self.assertGreaterEqual(row.finished_at, before["started_at"])
        for key, value in before.items():
            if key not in {"status", "error", "finished_at", "updated_at"}:
                self.assertEqual(getattr(row, key), value, key)
        receipt = await self.done(claim, result={"stale": True})
        self.assertEqual(receipt.acknowledgement, OutcomeAck.CLAIM_LOST)
        self.assertEqual(self.evidence(await self.read(job.id)), self.evidence(row))

    async def test_observed_snapshot_cannot_cancel_reclaimed_generation(self):
        job, old = await self.acquired()
        await self.mutate(job.id, status="pending", locked_at=None)
        with tenant_scope(self.own.id):
            newer = await self.manager.claim_job(job.id)
        self.assertGreater(newer.attempts, old.attempts)
        before = self.evidence(await self.read(job.id))
        self.assertFalse(await self.cancel(old))
        self.assertEqual(self.evidence(await self.read(job.id)), before)

    async def test_changed_identity_or_terminal_status_is_not_cancelled(self):
        for changes in (
            {"job_type": "reflect"},
            {"agent_task_id": self.task.id},
            {"started_at": None},
            {"status": "done", "result": {"winner": True}},
            {"status": "pending"},
            {"status": "failed"},
        ):
            with self.subTest(changes=changes):
                job, claim = await self.acquired()
                await self.mutate(job.id, **changes)
                before = self.evidence(await self.read(job.id))
                self.assertFalse(await self.cancel(claim))
                self.assertEqual(self.evidence(await self.read(job.id)), before)

    async def test_foreign_tenant_scope_and_bypass_do_not_cancel_other_workspace(self):
        job, claim = await self.acquired()
        before = self.evidence(await self.read(job.id))
        for scope in (None, self.other.id):
            with tenant_scope(scope), self.assertRaises(TenantContextError):
                await self.manager.cancel_running(claim)
        wrong = replace(claim, tenant_id=self.other.id)
        with tenant_scope(bypass=True):
            self.assertFalse(await self.manager.cancel_running(wrong))
        self.assertEqual(self.evidence(await self.read(job.id)), before)

    async def test_attached_task_snapshot_loses_if_task_link_is_removed(self):
        job, claim = await self.acquired(task_id=self.task.id)
        await self.mutate(job.id, agent_task_id=None)
        before = self.evidence(await self.read(job.id))
        self.assertFalse(await self.cancel(claim))
        self.assertEqual(self.evidence(await self.read(job.id)), before)

    async def test_heartbeat_does_not_invalidate_operator_snapshot(self):
        job, claim = await self.acquired()
        await self.mutate(job.id, locked_at=claim.started_at + timedelta(minutes=1))
        self.assertTrue(await self.cancel(claim))

    async def test_two_cancellers_have_one_acknowledged_transition(self):
        job, claim = await self.acquired()
        outcomes = await asyncio.wait_for(asyncio.gather(self.cancel(claim), self.cancel(claim)), timeout=20)
        self.assertEqual(sorted(outcomes), [False, True])
        self.assertEqual((await self.read(job.id)).status, "failed")

    async def test_competing_cancel_and_completion_do_not_overwrite_winner(self):
        job, claim = await self.acquired()
        ready = asyncio.Event()
        participants = 0

        def factory():
            session = async_session_maker()
            original = session.execute

            async def execute(statement, *args, **kwargs):
                nonlocal participants
                participants += 1
                if participants == 2:
                    ready.set()
                await asyncio.wait_for(ready.wait(), timeout=10)
                return await original(statement, *args, **kwargs)

            session.execute = execute
            return session

        with patch.object(database_module, "async_session_maker", factory):
            cancelled, receipt = await asyncio.wait_for(
                asyncio.gather(self.cancel(claim), self.done(claim, result={"winner": "worker"})), timeout=20
            )
        row = await self.read(job.id)
        if cancelled:
            self.assertEqual(receipt.acknowledgement, OutcomeAck.CLAIM_LOST)
            self.assertEqual((row.status, row.error), ("failed", "Cancelled by operator"))
            self.assertIsNone(row.result)
        else:
            self.assertEqual(receipt.acknowledgement, OutcomeAck.DONE)
            self.assertEqual((row.status, row.result), ("done", {"winner": "worker"}))
            self.assertIsNone(row.error)

    async def test_flush_failure_rolls_back_and_is_not_a_false_loss(self):
        job, claim = await self.acquired()
        before = self.evidence(await self.read(job.id))

        def factory():
            session = async_session_maker()

            def fail_after_flush(*args):
                raise RuntimeError("injected cancel flush failure")

            event.listen(session.sync_session, "after_flush", fail_after_flush)
            return session

        with patch.object(database_module, "async_session_maker", factory):
            with self.assertRaisesRegex(RuntimeError, "injected cancel flush failure"):
                await self.cancel(claim)
        self.assertEqual(self.evidence(await self.read(job.id)), before)

    async def test_committed_ack_loss_propagates_and_does_not_repeat_write(self):
        job, claim = await self.acquired()
        opened = 0

        class AckLossTransaction:
            def __init__(self, transaction):
                self.transaction = transaction

            async def __aenter__(self):
                return await self.transaction.__aenter__()

            async def __aexit__(self, kind, error, traceback):
                result = await self.transaction.__aexit__(kind, error, traceback)
                if kind is None:
                    raise ConnectionError("injected cancel acknowledgement loss")
                return result

        def factory():
            nonlocal opened
            opened += 1
            session = async_session_maker()
            begin = session.begin
            session.begin = lambda: AckLossTransaction(begin())
            return session

        with patch.object(database_module, "async_session_maker", factory):
            with self.assertRaisesRegex(ConnectionError, "injected cancel acknowledgement loss"):
                await self.cancel(claim)
        self.assertEqual(opened, 1)
        self.assertEqual((await self.read(job.id)).status, "failed")


if __name__ == "__main__":
    unittest.main()
