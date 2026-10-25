"""Prepared Job/Task transaction proofs against the existing reviewed test schema.

Owner: python tests/test_job_task_atomicity_db.py (sequential with all DB tests).
The shared fixture imports resolve_and_redirect before app modules. No conftest,
bootstrap, schema operations, provider calls or actual notifications occur here.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core import database as database_module
from app.core.database import async_session_maker
from app.core.permissions import service_permission_scope
from app.core.tenant_context import tenant_scope
from app.jobs import dispatcher
from app.jobs.claim_outcomes import OutcomeAck
from app.models import AgentTask


def _load_dep(name, path):
    # Cross-group helper module loader (absolute path; bare sibling imports do not
    # resolve across domain dirs).
    import pathlib

    filepath = pathlib.Path(__file__).resolve().parents[2] / path
    spec = importlib.util.spec_from_file_location(name, filepath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    # Same insertion pytest itself does for collected test modules so the loaded
    # helper's own bare sibling imports keep resolving.
    if str(filepath.parent) not in sys.path:
        sys.path.insert(0, str(filepath.parent))
    spec.loader.exec_module(module)
    return module


fixtures = _load_dep("_dep_job_claim_outcomes_db", "tests/jobs/test_job_claim_outcomes_db.py")


class AtomicityDatabaseTests(unittest.IsolatedAsyncioTestCase):
    # The real tenant guard, not the suite-wide bypass: this class creates two
    # workspaces and proves that a Job cannot write the summary of a task that
    # lives in another one. Under the bypass every row — the foreign task, the
    # job, the claim — lands in the bootstrap workspace, the "foreign" reference
    # stops being foreign and the proof silently stops testing anything.
    pytestmark = pytest.mark.tenancy
    # Reuse arrangement helpers, not the other suite's test methods (no duplicate discovery).
    asyncSetUp = fixtures.ClaimDatabaseTests.asyncSetUp
    cleanup_rows = fixtures.ClaimDatabaseTests.cleanup_rows
    acquired = fixtures.ClaimDatabaseTests.acquired
    read = fixtures.ClaimDatabaseTests.read
    done = fixtures.ClaimDatabaseTests.done
    fail = fixtures.ClaimDatabaseTests.fail
    evidence = staticmethod(fixtures.ClaimDatabaseTests.evidence)

    async def seed_summary(self):
        with tenant_scope(self.own.id), service_permission_scope("agenttask", "update"):
            await AgentTask.objects.update_by_id(
                self.task.id,
                last_status="prior",
                last_error="prior",
                last_run_at=fixtures.NOW,
            )

    async def task_state(self, tenant=None, task_id=None):
        tenant = tenant if tenant is not None else self.own
        task_id = task_id if task_id is not None else self.task.id
        with tenant_scope(tenant.id):
            task = await AgentTask.objects.get(id=task_id)
        return task.last_status, task.last_error, task.last_run_at, task.updated_at

    async def test_job_and_task_changes_are_invisible_until_the_same_commit(self):
        await self.seed_summary()
        job, claim = await self.acquired(task_id=self.task.id)
        job_before = self.evidence(await self.read(job.id))
        task_before = await self.task_state()
        ready, release = asyncio.Event(), asyncio.Event()
        original = self.manager._write_task_outcome

        async def hold_after_flush(session, owned_claim, **kwargs):
            await original(session, owned_claim, **kwargs)
            await session.flush()  # Both real UPDATEs reached PostgreSQL; neither committed.
            ready.set()
            await asyncio.wait_for(release.wait(), timeout=10)

        with patch.object(self.manager, "_write_task_outcome", hold_after_flush):
            writer = asyncio.create_task(self.done(claim, result={"status": "ok"}, llm_cost=0))
            try:
                await asyncio.wait_for(ready.wait(), timeout=10)
                self.assertEqual(self.evidence(await self.read(job.id)), job_before)
                self.assertEqual(await self.task_state(), task_before)
            finally:
                release.set()
                receipt = await asyncio.wait_for(writer, timeout=10)
        self.assertEqual(receipt.acknowledgement, OutcomeAck.DONE)
        stored = await self.read(job.id)
        task_after = await self.task_state()
        self.assertEqual(stored.status, "done")
        self.assertEqual(task_after[:2], ("ok", None))
        self.assertEqual(task_after[2], stored.finished_at)

    async def test_error_after_actual_task_flush_rolls_back_both_rows(self):
        await self.seed_summary()
        job, claim = await self.acquired(task_id=self.task.id)
        job_before = self.evidence(await self.read(job.id))
        task_before = await self.task_state()
        original = self.manager._write_task_outcome

        async def fail_after_flush(session, owned_claim, **kwargs):
            await original(session, owned_claim, **kwargs)
            await session.flush()
            raise RuntimeError("injected after coupled flush")

        with patch.object(self.manager, "_write_task_outcome", fail_after_flush):
            with self.assertRaisesRegex(RuntimeError, "injected after coupled flush"):
                await self.done(claim, result={"not_committed": True}, llm_cost=0.21)
        self.assertEqual(self.evidence(await self.read(job.id)), job_before)
        self.assertEqual(await self.task_state(), task_before)

    async def test_foreign_task_reference_rejects_zero_row_projection_and_rolls_back_job(self):
        with tenant_scope(self.other.id), service_permission_scope("agenttask", "create"):
            foreign = await AgentTask.objects.create(
                name=f"foreign-atomic-task-{uuid4().hex}",
                job_type="learn",
                cron_expr="0 0 * * *",
                payload={},
                is_active=False,
                last_status="foreign-prior",
                last_error="foreign-private",
            )
        # The FK exists, but points outside the Job's tenant: trusted corrupt-reference arrangement.
        job, claim = await self.acquired(task_id=foreign.id)
        job_before = self.evidence(await self.read(job.id))
        foreign_before = await self.task_state(self.other, foreign.id)
        with self.assertRaisesRegex(RuntimeError, "task outcome target unavailable") as raised:
            await self.done(claim, result={"not_committed": True})
        self.assertNotIn("foreign-private", str(raised.exception))
        self.assertEqual(self.evidence(await self.read(job.id)), job_before)
        self.assertEqual(await self.task_state(self.other, foreign.id), foreign_before)

    async def test_coupled_commit_ack_loss_stops_dispatch_without_replay_or_notification(self):
        await self.seed_summary()
        job, claim = await self.acquired(task_id=self.task.id)

        # The dispatch path commits twice before the outcome: the claim renewal
        # (one row) and then the coupled outcome (job + task). Only the second
        # one may lose its ACK — that is the "coupled commit" this test is
        # about. Injecting into the first would fail the renewal and prove
        # something else entirely.
        commits = {"count": 0}
        coupled_commit = 2

        class AckLossTransaction:
            def __init__(self, transaction):
                self.transaction = transaction

            async def __aenter__(self):
                return await self.transaction.__aenter__()

            async def __aexit__(self, kind, error, traceback):
                result = await self.transaction.__aexit__(kind, error, traceback)
                if kind is None:
                    commits["count"] += 1
                    if commits["count"] == coupled_commit:
                        raise ConnectionError("injected coupled commit ACK loss")
                return result

        def factory():
            session = async_session_maker()
            original = session.begin
            session.begin = lambda: AckLossTransaction(original())
            return session

        handler = AsyncMock(return_value={"status": "ok", "llm_cost": 0})
        notify = AsyncMock()
        task_writer = AsyncMock(wraps=self.manager._write_task_outcome)
        with patch.object(database_module, "async_session_maker", factory):
            with patch.object(dispatcher, "jobs", self.manager), patch.object(dispatcher, "_notify_job_result", notify):
                with patch.object(self.manager, "_write_task_outcome", task_writer):
                    with self.assertRaises(dispatcher.JobOutcomePersistenceError) as raised:
                        await dispatcher.execute_job(job, handler, allow_retry=False)
        self.assertEqual(raised.exception.error_code, "connection_error")
        handler.assert_awaited_once()
        task_writer.assert_awaited_once()
        notify.assert_not_awaited()
        stored = await self.read(job.id)
        self.assertEqual((stored.status, stored.result, stored.llm_cost), ("done", {"status": "ok", "llm_cost": 0}, 0))
        self.assertEqual((await self.task_state())[:2], ("ok", None))
        # This injected schedule committed both rows; real ACK loss never proves rollback.

    async def test_competing_terminal_finalizers_commit_one_matching_task_summary(self):
        await self.seed_summary()
        job, claim = await self.acquired(task_id=self.task.id)
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

        task_writer = AsyncMock(wraps=self.manager._write_task_outcome)
        with patch.object(database_module, "async_session_maker", factory):
            with patch.object(self.manager, "_write_task_outcome", task_writer):
                receipts = await asyncio.wait_for(
                    asyncio.gather(
                        self.done(claim, result={"winner": "success"}),
                        self.fail(claim, error="terminal handler failure", allow_retry=False),
                    ),
                    timeout=20,
                )
        winners = [receipt for receipt in receipts if receipt.acknowledgement != OutcomeAck.CLAIM_LOST]
        self.assertEqual(len(winners), 1)
        task_writer.assert_awaited_once()
        winner = winners[0]
        stored = await self.read(job.id)
        summary = await self.task_state()
        if winner.acknowledgement == OutcomeAck.DONE:
            self.assertEqual(
                (stored.status, stored.result, summary[0], summary[1]), ("done", {"winner": "success"}, "ok", None)
            )
        else:
            self.assertEqual(
                (stored.status, stored.error, summary[0], summary[1]),
                ("failed", "terminal handler failure", "failed", "terminal handler failure"),
            )
        self.assertEqual(summary[2], stored.finished_at)
        loser = next(receipt for receipt in receipts if receipt.acknowledgement == OutcomeAck.CLAIM_LOST)
        self.assertIsNone(loser.result)

    async def test_cancellation_after_task_flush_rolls_back_both_without_an_outcome_receipt(self):
        await self.seed_summary()
        job, claim = await self.acquired(task_id=self.task.id)
        job_before = self.evidence(await self.read(job.id))
        task_before = await self.task_state()
        original = self.manager._write_task_outcome

        async def cancel_after_flush(session, owned_claim, **kwargs):
            await original(session, owned_claim, **kwargs)
            await session.flush()
            raise asyncio.CancelledError()

        with patch.object(self.manager, "_write_task_outcome", cancel_after_flush):
            with self.assertRaises(asyncio.CancelledError):
                await self.done(claim, result={"not_committed": True})
        self.assertEqual(self.evidence(await self.read(job.id)), job_before)
        self.assertEqual(await self.task_state(), task_before)

    async def test_taskless_outcome_commits_without_a_detached_or_atomic_task_write(self):
        job, claim = await self.acquired()
        task_before = await self.task_state()
        with patch.object(self.manager, "_write_task_outcome", AsyncMock()) as atomic:
            with patch.object(self.manager, "_record_task_result", AsyncMock()) as detached:
                receipt = await self.done(claim, result={"status": "ok"})
        atomic.assert_not_awaited()
        detached.assert_not_awaited()
        self.assertEqual(receipt.acknowledgement, OutcomeAck.DONE)
        self.assertEqual((await self.read(job.id)).status, "done")
        self.assertEqual(await self.task_state(), task_before)

    async def test_trial_task_switches_itself_off_after_success(self):
        """A task created with `deactivate_after_success` stops after one good run.

        The flag is the owner's decision at creation; a task without it keeps
        its schedule even when the run succeeds.
        """
        with tenant_scope(self.own.id), service_permission_scope("agenttask", "update"):
            await AgentTask.objects.update_by_id(
                self.task.id, payload={"deactivate_after_success": True}, is_active=True
            )
        job, claim = await self.acquired(task_id=self.task.id)
        await self.done(claim, result={"ok": True})

        with tenant_scope(self.own.id):
            task = await AgentTask.objects.get(id=self.task.id)
        self.assertFalse(task.is_active, "the trial task must not be scheduled again")
        self.assertEqual(task.last_status, "ok")
        self.assertIsNone(task.next_run_at)

    async def test_a_regular_task_stays_scheduled_after_success(self):
        with tenant_scope(self.own.id), service_permission_scope("agenttask", "update"):
            await AgentTask.objects.update_by_id(self.task.id, payload={}, is_active=True)
        job, claim = await self.acquired(task_id=self.task.id)
        await self.done(claim, result={"ok": True})

        with tenant_scope(self.own.id):
            task = await AgentTask.objects.get(id=self.task.id)
        self.assertTrue(task.is_active, "a recurring task is unaffected by a success")


if __name__ == "__main__":
    unittest.main()
