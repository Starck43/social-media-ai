"""Prepared PostgreSQL claim/outcome regressions; owner execution only.

Owner: python tests/test_job_claim_outcomes_db.py
Uses an EXISTING reviewed test schema. No conftest/bootstrap/schema operations,
providers or sends. Concurrent sessions belong to one sequential test process.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.setup_test_db import resolve_and_redirect

resolve_and_redirect()  # Before app imports; refuses working DB/schema identity.

from sqlalchemy import event, update

from app.core import database as database_module
from app.core.database import async_engine, async_session_maker
from app.core.permissions import service_permission_scope
from app.core.tenant_context import TenantContextError, tenant_scope
from app.jobs.claim_outcomes import JobClaim, OutcomeAck
from app.models import AgentTask, Job, Tenant
from app.models.managers.job_manager import JobManager

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


class ClaimDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenants = []
        self.addAsyncCleanup(self.cleanup_rows)
        for _ in range(2):
            self.tenants.append(
                await Tenant.objects.create(
                    name="Claim outcome test",
                    slug=f"claim-outcome-{uuid4().hex}",
                    plan="business",
                )
            )
        self.own, self.other = self.tenants
        self.manager = JobManager()
        with tenant_scope(self.own.id):
            with service_permission_scope("agenttask", "create"):
                self.task = await AgentTask.objects.create(
                    name=f"claim-outcome-{uuid4().hex}",
                    job_type="learn",
                    cron_expr="0 0 * * *",
                    payload={},
                    is_active=False,
                )

    async def cleanup_rows(self):
        try:
            for tenant in reversed(self.tenants):
                await Tenant.objects.delete_by_id(tenant.id)
        finally:
            await async_engine.dispose()

    async def acquired(self, *, task_id=None):
        with tenant_scope(self.own.id):
            pending = await self.manager.enqueue("learn", agent_task_id=task_id, run_at=NOW, max_attempts=3)
            job = await self.manager.claim_job(pending.id)
        self.assertIsNotNone(job)
        self.assertEqual(job.status, "running")
        return job, JobClaim.capture(job)

    async def read(self, job_id):
        with tenant_scope(self.own.id):
            return await self.manager.get(id=job_id)

    async def mutate(self, job_id, **values):
        # Trusted arrangement, restricted to this test's own tenant and Job.
        async with async_session_maker() as session:
            async with session.begin():
                await session.execute(
                    update(Job)
                    .where(
                        Job.id == job_id,
                        Job.tenant_id == self.own.id,
                    )
                    .values(**values)
                )

    async def done(self, claim, **kwargs):
        with tenant_scope(claim.tenant_id):
            return await self.manager.mark_done(claim.job_id, claim=claim, **kwargs)

    async def fail(self, claim, **kwargs):
        with tenant_scope(claim.tenant_id):
            return await self.manager.mark_failed(claim.job_id, claim=claim, **kwargs)

    @staticmethod
    def evidence(job):
        return {
            key: getattr(job, key)
            for key in (
                "tenant_id",
                "job_type",
                "agent_task_id",
                "status",
                "attempts",
                "started_at",
                "locked_at",
                "run_at",
                "finished_at",
                "result",
                "error",
                "llm_cost",
                "updated_at",
            )
        }

    async def test_owned_success_commits_zero_cost_and_projects_task(self):
        job, claim = await self.acquired(task_id=self.task.id)
        receipt = await self.done(claim, result={"status": "ok"}, llm_cost=0)
        self.assertEqual(receipt.acknowledgement, OutcomeAck.DONE)
        self.assertFalse(receipt.task_projection_failed)
        row = await self.read(job.id)
        self.assertEqual((row.status, row.result, row.llm_cost), ("done", {"status": "ok"}, 0))
        with tenant_scope(self.own.id):
            task = await AgentTask.objects.get(id=self.task.id)
        self.assertEqual(task.last_status, "ok")

    async def test_old_worker_after_reap_before_reclaim_cannot_write(self):
        job, claim = await self.acquired()
        await self.mutate(job.id, status="pending", locked_at=None, result={"audit": "keep"}, error="keep")
        before = self.evidence(await self.read(job.id))
        receipt = await self.done(claim, result={"old": True}, llm_cost=99)
        self.assertEqual(receipt.acknowledgement, OutcomeAck.CLAIM_LOST)
        self.assertEqual(self.evidence(await self.read(job.id)), before)
        self.assertIsNone(receipt.result)

    async def test_old_success_and_failure_cannot_overwrite_new_generation_or_retry_budget(self):
        job, old_claim = await self.acquired()
        await self.mutate(job.id, status="pending", locked_at=None, run_at=NOW)
        with tenant_scope(self.own.id):
            new_job = await self.manager.claim_job(job.id)
        self.assertEqual(new_job.attempts, old_claim.attempts + 1)
        before = self.evidence(await self.read(job.id))
        success = await self.done(old_claim, result={"old": True}, llm_cost=99)
        failure = await self.fail(old_claim, error="old failure", allow_retry=True, llm_cost=99)
        self.assertEqual(success.acknowledgement, OutcomeAck.CLAIM_LOST)
        self.assertEqual(failure.acknowledgement, OutcomeAck.CLAIM_LOST)
        self.assertEqual(self.evidence(await self.read(job.id)), before)

    async def test_changed_type_task_start_time_and_cancelled_status_lose_without_projection(self):
        cases = [
            {"job_type": "reflect"},
            {"agent_task_id": self.task.id},
            {"started_at": NOW - timedelta(days=1)},
            {"status": "failed", "error": "cancelled"},
        ]
        for values in cases:
            with self.subTest(values=values):
                job, claim = await self.acquired()
                await self.mutate(job.id, **values)
                before = self.evidence(await self.read(job.id))
                with patch.object(self.manager, "_write_task_outcome", AsyncMock()) as project:
                    receipt = await self.done(claim, result={"old": True})
                self.assertEqual(receipt.acknowledgement, OutcomeAck.CLAIM_LOST)
                self.assertEqual(self.evidence(await self.read(job.id)), before)
                project.assert_not_awaited()

    async def test_wrong_claim_tenant_and_missing_row_disclose_no_result(self):
        job, claim = await self.acquired()
        wrong = replace(claim, tenant_id=self.other.id)
        receipt = await self.done(wrong, result={"foreign": True})
        self.assertEqual(receipt.acknowledgement, OutcomeAck.CLAIM_LOST)
        self.assertIsNone(receipt.result)
        with tenant_scope(self.own.id):
            await self.manager.delete_by_id(job.id)
        self.assertEqual((await self.done(claim)).acknowledgement, OutcomeAck.CLAIM_LOST)

    async def test_context_mismatch_and_missing_scope_fail_closed(self):
        job, claim = await self.acquired()
        before = self.evidence(await self.read(job.id))
        for context in (None, self.other.id):
            with tenant_scope(context), self.assertRaises(TenantContextError):
                await self.manager.mark_done(job.id, claim=claim)
        self.assertEqual(self.evidence(await self.read(job.id)), before)

    async def test_bypass_is_still_bound_to_concrete_tenant(self):
        job, claim = await self.acquired()
        wrong = replace(claim, tenant_id=self.other.id)
        with tenant_scope(bypass=True):
            receipt = await self.manager.mark_done(job.id, claim=wrong, result={"wrong": True})
        self.assertEqual(receipt.acknowledgement, OutcomeAck.CLAIM_LOST)
        self.assertEqual((await self.read(job.id)).status, "running")

    async def test_heartbeat_change_preserves_acquired_identity(self):
        job, claim = await self.acquired()
        await self.mutate(job.id, locked_at=claim.started_at + timedelta(minutes=1))
        self.assertEqual((await self.done(claim)).acknowledgement, OutcomeAck.DONE)

    async def test_terminal_returned_failure_retains_known_cost_and_projects_failed_task(self):
        job, claim = await self.acquired(task_id=self.task.id)
        receipt = await self.fail(
            claim,
            error="invalid_structured_output",
            allow_retry=False,
            result={"status": "failed", "llm_cost": 0.12},
            llm_cost=0.12,
        )
        self.assertEqual(receipt.acknowledgement, OutcomeAck.FAILED)
        row = await self.read(job.id)
        self.assertEqual(row.status, "failed")
        self.assertAlmostEqual(row.llm_cost, 0.12)
        with tenant_scope(self.own.id):
            task = await AgentTask.objects.get(id=self.task.id)
        self.assertEqual((task.last_status, task.last_error), ("failed", "invalid_structured_output"))

    async def test_retry_uses_this_generation_and_does_not_project_terminal_task(self):
        job, claim = await self.acquired(task_id=self.task.id)
        with patch.object(self.manager, "_write_task_outcome", AsyncMock()) as project:
            receipt = await self.fail(claim, error="handler failed", allow_retry=True)
        self.assertEqual(receipt.acknowledgement, OutcomeAck.RETRY)
        row = await self.read(job.id)
        self.assertEqual(row.status, "pending")
        self.assertEqual(row.attempts, claim.attempts)
        self.assertIsNone(row.llm_cost)
        project.assert_not_awaited()

    async def test_future_or_already_acquired_job_cannot_be_claimed_again(self):
        with tenant_scope(self.own.id):
            pending = await self.manager.enqueue("learn", run_at=datetime.now(timezone.utc) + timedelta(days=1))
            self.assertIsNone(await self.manager.claim_job(pending.id))
        job, claim = await self.acquired()
        with tenant_scope(self.own.id):
            self.assertIsNone(await self.manager.claim_job(job.id))
        self.assertEqual(JobClaim.capture(await self.read(job.id)), claim)

    async def test_two_concurrent_finalizers_have_exactly_one_committed_receipt(self):
        job, claim = await self.acquired()
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

        with patch.object(database_module, "async_session_maker", factory):
            outcomes = await asyncio.wait_for(
                asyncio.gather(
                    self.done(claim, result={"winner": "one"}),
                    self.done(claim, result={"winner": "two"}),
                ),
                timeout=20,
            )
        self.assertEqual(
            sorted(receipt.acknowledgement.value for receipt in outcomes),
            sorted([OutcomeAck.DONE.value, OutcomeAck.CLAIM_LOST.value]),
        )
        winner = next(receipt for receipt in outcomes if receipt.acknowledgement == OutcomeAck.DONE)
        loser = next(receipt for receipt in outcomes if receipt.acknowledgement == OutcomeAck.CLAIM_LOST)
        self.assertEqual((await self.read(job.id)).result, winner.result)
        self.assertIsNone(loser.result)

    async def test_error_after_flush_rolls_back_and_never_projects_task(self):
        job, claim = await self.acquired()
        before = self.evidence(await self.read(job.id))

        def factory():
            session = async_session_maker()

            def fail_after_flush(*args):
                raise RuntimeError("injected outcome flush failure")

            event.listen(session.sync_session, "after_flush", fail_after_flush)
            return session

        with patch.object(database_module, "async_session_maker", factory):
            with patch.object(self.manager, "_write_task_outcome", AsyncMock()) as project:
                with self.assertRaisesRegex(RuntimeError, "injected outcome flush failure"):
                    await self.done(claim, result={"not_committed": True})
        project.assert_not_awaited()
        self.assertEqual(self.evidence(await self.read(job.id)), before)

    async def test_task_write_failure_rolls_back_job_and_returns_no_receipt(self):
        job, claim = await self.acquired(task_id=self.task.id)
        before = self.evidence(await self.read(job.id))
        with patch.object(self.manager, "_write_task_outcome", AsyncMock(side_effect=RuntimeError("private error"))):
            with self.assertRaisesRegex(RuntimeError, "private error"):
                await self.done(claim, result={"status": "ok"})
        self.assertEqual(self.evidence(await self.read(job.id)), before)
        with tenant_scope(self.own.id):
            task = await AgentTask.objects.get(id=self.task.id)
        self.assertIsNone(task.last_status)
        self.assertIsNone(task.last_error)

    async def test_commit_ack_loss_is_an_exception_not_rollback_or_claim_loss(self):
        job, claim = await self.acquired()

        class AckLossTransaction:
            def __init__(self, transaction):
                self.transaction = transaction

            async def __aenter__(self):
                return await self.transaction.__aenter__()

            async def __aexit__(self, kind, error, traceback):
                result = await self.transaction.__aexit__(kind, error, traceback)
                if kind is None:
                    raise ConnectionError("injected lost commit acknowledgement")
                return result

        def factory():
            session = async_session_maker()
            original = session.begin
            session.begin = lambda: AckLossTransaction(original())
            return session

        with patch.object(database_module, "async_session_maker", factory):
            with patch.object(self.manager, "_write_task_outcome", AsyncMock()) as project:
                with self.assertRaisesRegex(ConnectionError, "injected lost commit acknowledgement"):
                    await self.done(claim, result={"committed": True})
        self.assertEqual((await self.read(job.id)).status, "done")
        project.assert_not_awaited()
        # This observation is specific to this injected schedule, not a general rollback inference.


if __name__ == "__main__":
    unittest.main()
