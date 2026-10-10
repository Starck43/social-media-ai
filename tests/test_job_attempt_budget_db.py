"""Prepared acquisition/recovery/retention regressions; owner execution only.

Use the existing reviewed schema and exported private environment. No conftest,
bootstrap/reset/migrations/providers/live. Concurrent sessions belong to one
sequential process. All arrangements and cleanup target this test's own rows.
"""

import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.setup_test_db import resolve_and_redirect

resolve_and_redirect()

from sqlalchemy import event, update

from app.core.database import async_engine, async_session_maker
from app.core.tenant_context import TenantContextError, tenant_scope
from app.jobs.attempt_budget import ATTEMPT_BUDGET_STOP_PREFIX
from app.jobs.claim_outcomes import JobClaim, OutcomeAck
from app.jobs.handlers import handle_prune
from app.models import Job, Tenant
from app.models.managers import base_manager as base_module
from app.models.managers import job_manager as manager_module
from app.models.managers.job_manager import JobManager

NOW = datetime.now(timezone.utc)
STALE = NOW - timedelta(minutes=31)


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class AttemptBudgetDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenants = []
        self.addAsyncCleanup(self.cleanup_rows)
        self.clock = patch.object(manager_module, "datetime", FixedDatetime)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        for _ in range(2):
            self.tenants.append(
                await Tenant.objects.create(
                    name="Attempt budget test", slug=f"attempt-budget-{uuid4().hex}", plan="business"
                )
            )
        self.own, self.other = self.tenants

    async def cleanup_rows(self):
        try:
            for tenant in reversed(self.tenants):
                await Tenant.objects.delete_by_id(tenant.id)
        finally:
            await async_engine.dispose()

    async def make_job(self, tenant=None, **changes):
        tenant = tenant or self.own
        values = dict(
            job_type="learn",
            payload={"audit": "keep"},
            status="pending",
            run_at=NOW,
            attempts=0,
            max_attempts=3,
            result={"audit": "retained"},
            error="previous evidence",
            llm_cost=0.25,
        )
        with tenant_scope(tenant.id):
            return await Job.objects.create(**(values | changes))

    async def read(self, job, tenant=None):
        with tenant_scope((tenant or self.own).id):
            return await Job.objects.get(id=job.id)

    async def reap(self, **kwargs):
        with tenant_scope(self.own.id):
            return await JobManager().reap_stale(**kwargs)

    async def test_claim_first_and_last_allowed_attempt_then_refuses_exhausted_row(self):
        for attempts in (0, 2):
            job = await self.make_job(attempts=attempts)
            claimed = await JobManager.claim_job(job.id, now=NOW)
            self.assertEqual(claimed.attempts, attempts + 1)
        for changes in ({"attempts": 3}, {"attempts": 4}, {"max_attempts": 0}, {"max_attempts": -1}, {"attempts": -1}):
            with self.subTest(changes=changes):
                job = await self.make_job(**changes)
                self.assertIsNone(await JobManager.claim_job(job.id, now=NOW))
                stored = await self.read(job)
                self.assertEqual((stored.status, stored.attempts), ("pending", job.attempts))
                self.assertIsNone(stored.started_at)

    async def test_exhausted_oldest_row_does_not_starve_an_eligible_job(self):
        exhausted = await self.make_job(attempts=3, run_at=NOW - timedelta(minutes=1))
        eligible = await self.make_job(attempts=2)
        # Bound global-worker selection to fixture IDs, never retained owner data.
        claimed = await JobManager._claim(Job.id.in_([exhausted.id, eligible.id]), now=NOW)
        self.assertEqual((claimed.id, claimed.attempts), (eligible.id, 3))
        self.assertEqual((await self.read(exhausted)).status, "pending")

    async def test_legacy_start_running_uses_the_same_cap(self):
        for attempts, expected in ((2, True), (3, False)):
            job = await self.make_job(attempts=attempts)
            self.assertEqual(await JobManager.start_running(job.id, now=NOW), expected)
            stored = await self.read(job)
            self.assertEqual(stored.attempts, attempts + int(expected))
            self.assertEqual(stored.status, "running" if expected else "pending")

    async def test_stale_requeue_then_final_budget_stop_retains_evidence_and_rejects_old_worker(self):
        job = await self.make_job(status="running", attempts=2, locked_at=STALE, started_at=STALE)
        self.assertEqual(await self.reap(), 1)
        pending = await self.read(job)
        self.assertEqual((pending.attempts, pending.locked_at, pending.error), (2, None, "previous evidence"))
        claimed = await JobManager.claim_job(job.id, now=NOW)
        claim = JobClaim.capture(claimed)
        self.assertEqual(claim.attempts, 3)
        async with async_session_maker() as session:
            async with session.begin():
                await session.execute(
                    update(Job).where(Job.id == job.id, Job.tenant_id == self.own.id).values(locked_at=STALE)
                )
        self.assertEqual(await self.reap(), 0)  # Return value counts requeues, not budget stops.
        stopped = await self.read(job)
        self.assertEqual(stopped.status, "failed")
        self.assertTrue(stopped.error.startswith(ATTEMPT_BUDGET_STOP_PREFIX))
        self.assertTrue(stopped.error.endswith("previous evidence"))
        self.assertEqual(stopped.finished_at, NOW)
        self.assertEqual(
            (stopped.attempts, stopped.started_at, stopped.locked_at, stopped.run_at),
            (3, claim.started_at, STALE, job.run_at),
        )
        self.assertEqual((stopped.payload, stopped.result, stopped.llm_cost), (job.payload, job.result, job.llm_cost))
        with tenant_scope(self.own.id):
            receipt = await JobManager().mark_done(job.id, claim=claim, result={"late": True}, llm_cost=99)
        self.assertEqual(receipt.acknowledgement, OutcomeAck.CLAIM_LOST)
        self.assertEqual((await self.read(job)).llm_cost, job.llm_cost)
        self.assertIsNone(await JobManager.claim_job(job.id, now=NOW))

    async def test_due_exhausted_and_invalid_pending_rows_stop_without_an_execution(self):
        for changes in ({"attempts": 3}, {"max_attempts": 0}, {"attempts": -1}):
            job = await self.make_job(error=None, **changes)
            self.assertEqual(await self.reap(), 0)
            stopped = await self.read(job)
            self.assertEqual((stopped.status, stopped.started_at), ("failed", None))
            self.assertEqual(stopped.attempts, job.attempts)
            self.assertEqual(stopped.error, ATTEMPT_BUDGET_STOP_PREFIX)

    async def test_fresh_null_boundary_and_future_rows_are_not_stopped(self):
        cases = (
            {"status": "running", "attempts": 3, "locked_at": NOW},
            {"status": "running", "attempts": 3, "locked_at": None},
            {"status": "running", "attempts": 3, "locked_at": NOW - timedelta(minutes=30)},
            {"attempts": 3, "run_at": NOW + timedelta(hours=1)},
            {"status": "done", "attempts": 3, "locked_at": STALE},
            {"status": "failed", "attempts": 3, "locked_at": STALE},
        )
        for changes in cases:
            job = await self.make_job(**changes)
            self.assertEqual(await self.reap(), 0)
            stored = await self.read(job)
            self.assertEqual((stored.status, stored.error, stored.locked_at), (job.status, job.error, job.locked_at))

    async def test_stop_preserves_known_zero_and_unknown_cost(self):
        for cost in (0.0, None):
            job = await self.make_job(attempts=3, llm_cost=cost)
            self.assertEqual(await self.reap(), 0)
            self.assertEqual((await self.read(job)).llm_cost, cost)

    async def test_tenant_scope_and_missing_scope_fail_closed(self):
        own = await self.make_job(attempts=3)
        other = await self.make_job(self.other, attempts=3)
        with tenant_scope(None), self.assertRaises(TenantContextError):
            await JobManager().reap_stale()
        self.assertEqual((await self.read(own)).status, "pending")
        await self.reap()
        self.assertEqual((await self.read(own)).status, "failed")
        self.assertEqual((await self.read(other, self.other)).status, "pending")

    async def test_explicit_bypass_is_limited_to_fixture_ids(self):
        rows = [await self.make_job(tenant, attempts=3) for tenant in self.tenants]
        with tenant_scope(bypass=True):
            manager = JobManager()
            manager.filter = manager.filter(id__in=[job.id for job in rows]).filter
            self.assertEqual(await manager.reap_stale(), 0)
        for tenant, job in zip(self.tenants, rows):
            self.assertEqual((await self.read(job, tenant)).status, "failed")

    async def test_two_reapers_do_not_double_requeue_or_duplicate_stop_marker(self):
        stopped = await self.make_job(status="running", attempts=3, locked_at=STALE, started_at=STALE)
        requeued = await self.make_job(status="running", attempts=2, locked_at=STALE, started_at=STALE)
        counts = await asyncio.wait_for(asyncio.gather(self.reap(), self.reap()), timeout=20)
        self.assertEqual(sorted(counts), [0, 1])
        self.assertEqual((await self.read(requeued)).status, "pending")
        row = await self.read(stopped)
        self.assertEqual(row.status, "failed")
        self.assertEqual(row.error.count(ATTEMPT_BUDGET_STOP_PREFIX), 1)

    async def competing_budget_stop(self, changes):
        job = await self.make_job(status="running", attempts=3, started_at=STALE, locked_at=STALE)
        issued = asyncio.Event()
        reaper = None
        async with async_session_maker() as reap_session:
            connection = await reap_session.connection()

            def before_execute(conn, cursor, statement, parameters, context, executemany):
                if context.compiled is not None and context.compiled.isupdate:
                    issued.set()

            event.listen(connection.sync_connection, "before_cursor_execute", before_execute)
            try:
                async with async_session_maker() as holder:
                    async with holder.begin():
                        await holder.execute(
                            update(Job).where(Job.id == job.id, Job.tenant_id == self.own.id).values(**changes)
                        )
                        with tenant_scope(self.own.id):
                            manager = JobManager()
                            manager.filter = manager.get_queryset(session=reap_session).filter
                            reaper = asyncio.create_task(manager.reap_stale())
                        await asyncio.wait_for(issued.wait(), timeout=5)
                    self.assertEqual(await asyncio.wait_for(asyncio.shield(reaper), timeout=5), 0)
            finally:
                if reaper is not None and not reaper.done():
                    reaper.cancel()
                    await asyncio.gather(reaper, return_exceptions=True)
                event.remove(connection.sync_connection, "before_cursor_execute", before_execute)
        row = await self.read(job)
        self.assertFalse(row.error.startswith(ATTEMPT_BUDGET_STOP_PREFIX))
        for key, value in changes.items():
            self.assertEqual(getattr(row, key), value)

    async def test_budget_stop_rechecks_competing_completion_and_heartbeat(self):
        for changes in ({"status": "done", "finished_at": NOW}, {"locked_at": NOW}):
            with self.subTest(changes=changes):
                await self.competing_budget_stop(changes)

    async def test_prune_keeps_budget_stop_but_deletes_ordinary_old_null_errors(self):
        old = NOW - timedelta(days=8)
        protected = await self.make_job(attempts=3, created_at=old)
        await self.reap()
        ordinary = [
            await self.make_job(status=status, error=error, created_at=old)
            for status, error in (("done", None), ("failed", None), ("failed", "known failure"))
        ]
        with tenant_scope(self.own.id):
            result = await handle_prune({"days": 7})
        self.assertEqual(result["deleted"], 3)
        self.assertIsNotNone(await self.read(protected))
        for job in ordinary:
            self.assertIsNone(await self.read(job))

    async def test_stop_write_failure_rolls_back_without_requeue(self):
        job = await self.make_job(attempts=3)

        def factory():
            session = async_session_maker()

            def after_execute(conn, cursor, statement, parameters, context, executemany):
                if context.compiled is not None and context.compiled.isupdate:
                    raise RuntimeError("injected budget stop write failure")

            event.listen(
                session.sync_session,
                "after_begin",
                lambda session, tx, conn: event.listen(conn, "after_cursor_execute", after_execute),
            )
            return session

        with patch.object(base_module, "async_session_maker", factory):
            with self.assertRaisesRegex(RuntimeError, "injected budget stop write failure"):
                await self.reap()
        row = await self.read(job)
        self.assertEqual(
            (row.status, row.error, row.attempts, row.result, row.llm_cost),
            (job.status, job.error, job.attempts, job.result, job.llm_cost),
        )
        self.assertIsNone(row.finished_at)

    async def test_committed_stop_ack_loss_propagates_without_requeue_or_repeat(self):
        job = await self.make_job(attempts=3)
        sessions = 0
        updates = 0

        def factory():
            nonlocal sessions
            sessions += 1
            session = async_session_maker()
            commit = session.commit

            async def uncertain_commit():
                await commit()
                raise ConnectionError("injected budget stop acknowledgement loss")

            def after_execute(conn, cursor, statement, parameters, context, executemany):
                nonlocal updates
                if context.compiled is not None and context.compiled.isupdate:
                    updates += 1

            event.listen(
                session.sync_session,
                "after_begin",
                lambda session, tx, conn: event.listen(conn, "after_cursor_execute", after_execute),
            )
            session.commit = uncertain_commit
            return session

        with patch.object(base_module, "async_session_maker", factory):
            with self.assertRaisesRegex(ConnectionError, "injected budget stop acknowledgement loss"):
                await self.reap()
        self.assertEqual((sessions, updates), (1, 1))
        row = await self.read(job)
        self.assertEqual(row.status, "failed")
        self.assertTrue(row.error.startswith(ATTEMPT_BUDGET_STOP_PREFIX))


if __name__ == "__main__":
    unittest.main()
