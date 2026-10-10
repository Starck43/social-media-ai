"""Prepared source-isolated claim/finalization tests. Owner runs this file.

No app imports, DB/conftest, providers or sends. Session doubles establish control
flow, not PostgreSQL locking/rollback. Real concurrency is in the separate DB file.
"""

import asyncio
import unittest
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import test_returned_job_failures as fixtures

CLAIMS = fixtures.CLAIMS
NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
PRIVATE = "PRIVATE-CUSTOMER-AND-SQL"


def row(**changes):
    values = dict(id=7, tenant_id=31, job_type="learn", agent_task_id=9, status="running", attempts=2,
                  started_at=NOW, locked_at=NOW, max_attempts=3, result=None, error=None, llm_cost=None,
                  run_at=NOW, finished_at=None, payload={})
    return SimpleNamespace(**(values | changes))


class ClaimTests(unittest.TestCase):
    def test_claim_is_immutable_and_heartbeat_is_not_identity(self):
        acquired = row()
        claim = CLAIMS.JobClaim.capture(acquired)
        acquired.locked_at = NOW + timedelta(minutes=1)
        self.assertEqual(CLAIMS.JobClaim.capture(acquired), claim)
        with self.assertRaises(FrozenInstanceError):
            claim.attempts = 99

    def test_invalid_or_legacy_identity_is_rejected(self):
        for changes in ({"id": True}, {"tenant_id": None}, {"attempts": 0}, {"attempts": "1"},
                        {"agent_task_id": False}, {"started_at": None}, {"started_at": NOW.replace(tzinfo=None)},
                        {"job_type": ""}, {"status": "pending"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                CLAIMS.JobClaim.capture(row(**changes))

    def test_claim_loss_receipt_contains_no_new_generation_result(self):
        claim = CLAIMS.JobClaim.capture(row())
        receipt = CLAIMS.JobOutcomeReceipt(claim, CLAIMS.OutcomeAck.CLAIM_LOST, error="Job claim no longer owned")
        self.assertEqual(receipt.as_outcome()["status"], "claim_lost")
        self.assertIsNone(receipt.as_outcome()["result"])

    def test_receipt_repr_excludes_result_and_error(self):
        receipt = CLAIMS.JobOutcomeReceipt(CLAIMS.JobClaim.capture(row()), CLAIMS.OutcomeAck.FAILED,
                                          result={"response": PRIVATE}, error=PRIVATE)
        self.assertNotIn(PRIVATE, repr(receipt))


class Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return self.name, value

    def is_not_distinct_from(self, value):
        return self.name, value


class Query:
    def __init__(self, model):
        self.criteria = []
        self.locked = False

    def where(self, *criteria):
        self.criteria.extend(criteria)
        return self

    def with_for_update(self):
        self.locked = True
        return self


class Transaction:
    def __init__(self, fixture):
        self.fixture = fixture

    async def __aenter__(self):
        return self

    async def __aexit__(self, kind, error, traceback):
        if kind is None and self.fixture.commit_error is not None:
            raise self.fixture.commit_error
        self.fixture.committed = kind is None


class Session:
    def __init__(self, fixture):
        self.fixture = fixture

    async def __aenter__(self):
        self.fixture.sessions += 1
        return self

    async def __aexit__(self, *args):
        return False

    def begin(self):
        return Transaction(self.fixture)

    async def execute(self, query):
        self.fixture.queries.append(query)
        matched = self.fixture.stored
        if matched is not None and not all(getattr(matched, name) == value for name, value in query.criteria):
            matched = None
        return SimpleNamespace(scalar_one_or_none=lambda: matched)


class ManagerBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stored = row()
        self.claim = CLAIMS.JobClaim.capture(self.stored)
        self.tenant_id = 31
        self.bypass = False
        self.sessions = 0
        self.queries = []
        self.committed = False
        self.commit_error = None
        model = SimpleNamespace(**{name: Column(name) for name in (
            "id", "tenant_id", "job_type", "agent_task_id", "status", "attempts", "started_at",
        )})

        class Base:
            def __class_getitem__(cls, item):
                return cls

            def __init__(self, model):
                self.model = model

        class TenantError(RuntimeError):
            pass

        self.TenantError = TenantError

        @contextmanager
        def scope(tenant_id):
            previous = self.tenant_id
            self.tenant_id = tenant_id
            try:
                yield
            finally:
                self.tenant_id = previous

        modules = {
            "app.models.managers.base_manager": fixtures.module_with(BaseManager=Base),
            "app.models.job": fixtures.module_with(Job=model),
            "sqlalchemy": fixtures.module_with(select=Query),
            "app.core.database": fixtures.module_with(async_session_maker=lambda: Session(self)),
            "app.core.config": fixtures.module_with(settings=SimpleNamespace(JOB_RETRY_BACKOFF_SECONDS=300)),
            "app.core.tenant_context": fixtures.module_with(
                TenantContextError=TenantError, current_tenant_id=lambda: self.tenant_id,
                is_bypass=lambda: self.bypass, tenant_scope=scope,
            ),
            "app.jobs.claim_outcomes": CLAIMS,
        }
        module = fixtures.load_source("app.models.managers._claim_manager_test",
                                      "app/models/managers/job_manager.py", modules)
        self.manager = module.JobManager()
        self.manager._record_task_result = AsyncMock(side_effect=self.project)

    async def project(self, claim, **kwargs):
        self.assertTrue(self.committed)
        self.assertEqual(claim, self.claim)
        self.assertEqual(self.tenant_id, claim.tenant_id)

    async def test_success_locks_full_identity_and_commits_before_projection(self):
        receipt = await self.manager.mark_done(7, result={"status": "ok"}, llm_cost=0, claim=self.claim)
        self.assertEqual(receipt.acknowledgement, CLAIMS.OutcomeAck.DONE)
        self.assertEqual(self.stored.status, "done")
        self.assertEqual(self.stored.llm_cost, 0)
        self.assertTrue(self.queries[0].locked)
        self.assertEqual(dict(self.queries[0].criteria), dict(id=7, tenant_id=31, job_type="learn",
                         agent_task_id=9, status="running", attempts=2, started_at=NOW))
        self.manager._record_task_result.assert_awaited_once_with(self.claim, status="ok", error=None)

    async def test_each_identity_change_or_nonrunning_status_loses_without_mutation(self):
        for changes in ({"status": "pending"}, {"status": "done"}, {"status": "failed"}, {"attempts": 3},
                        {"started_at": NOW + timedelta(seconds=1)}, {"agent_task_id": None},
                        {"tenant_id": 77}, {"job_type": "reflect"}, {"id": 8}):
            with self.subTest(changes=changes):
                self.stored = row(**changes)
                before = deepcopy(vars(self.stored))
                receipt = await self.manager.mark_failed(7, "old", result={"old": True}, llm_cost=99,
                                                        claim=self.claim)
                self.assertEqual(receipt.acknowledgement, CLAIMS.OutcomeAck.CLAIM_LOST)
                self.assertEqual(vars(self.stored), before)
        self.manager._record_task_result.assert_not_awaited()

    async def test_missing_row_loses_without_projection(self):
        self.stored = None
        receipt = await self.manager.mark_done(7, claim=self.claim)
        self.assertEqual(receipt.acknowledgement, CLAIMS.OutcomeAck.CLAIM_LOST)
        self.manager._record_task_result.assert_not_awaited()

    async def test_heartbeat_change_does_not_revoke_generation(self):
        self.stored.locked_at = NOW + timedelta(minutes=1)
        self.assertEqual((await self.manager.mark_done(7, claim=self.claim)).acknowledgement, CLAIMS.OutcomeAck.DONE)

    async def test_retry_uses_acquired_attempt_and_retains_existing_budget_policy(self):
        receipt = await self.manager.mark_failed(7, "handler failed", claim=self.claim)
        self.assertEqual(receipt.acknowledgement, CLAIMS.OutcomeAck.RETRY)
        self.assertEqual(self.stored.status, "pending")
        # This test deliberately avoids wall-time equality; exact DB test checks generation policy.
        self.assertGreaterEqual(self.stored.run_at, datetime.now(timezone.utc) + timedelta(seconds=590))
        self.manager._record_task_result.assert_not_awaited()

    async def test_inline_failure_is_terminal_and_unknown_cost_stays_unknown(self):
        receipt = await self.manager.mark_failed(7, "failed", allow_retry=False,
                                                result={"status": "failed"}, claim=self.claim)
        self.assertEqual(receipt.acknowledgement, CLAIMS.OutcomeAck.FAILED)
        self.assertIsNone(self.stored.llm_cost)
        self.manager._record_task_result.assert_awaited_once_with(self.claim, status="failed", error="failed")

    async def test_receipt_result_is_a_detached_snapshot(self):
        result = {"facts": ["brief"]}
        receipt = await self.manager.mark_done(7, result=result, claim=self.claim)
        result["facts"].append("changed")
        self.assertEqual(receipt.result, {"facts": ["brief"]})

    async def test_scope_mismatch_or_missing_context_fails_before_session(self):
        for tenant_id in (None, 77):
            self.tenant_id = tenant_id
            with self.assertRaises(self.TenantError):
                await self.manager.mark_done(7, claim=self.claim)
        self.assertEqual(self.sessions, 0)

    async def test_explicit_bypass_still_uses_concrete_claim_tenant(self):
        self.tenant_id = None
        self.bypass = True
        self.stored.tenant_id = 77
        receipt = await self.manager.mark_done(7, claim=self.claim)
        self.assertEqual(receipt.acknowledgement, CLAIMS.OutcomeAck.CLAIM_LOST)
        self.assertIn(("tenant_id", 31), self.queries[0].criteria)

    async def test_commit_exception_is_not_lost_claim_or_projection(self):
        self.commit_error = RuntimeError(PRIVATE)
        with self.assertRaisesRegex(RuntimeError, PRIVATE):
            await self.manager.mark_done(7, claim=self.claim)
        self.assertEqual(self.sessions, 1)
        self.manager._record_task_result.assert_not_awaited()

    async def test_task_projection_failure_retains_committed_receipt(self):
        self.manager._record_task_result.side_effect = RuntimeError(PRIVATE)
        receipt = await self.manager.mark_done(7, result={"status": "ok"}, claim=self.claim)
        self.assertTrue(receipt.task_projection_failed)
        self.assertEqual(receipt.acknowledgement, CLAIMS.OutcomeAck.DONE)
        self.assertEqual(self.stored.status, "done")
        self.assertNotIn(PRIVATE, repr(receipt))


class DispatcherClaimTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.DispatcherTests("test_success_remains_success")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.dispatcher = self.fixture.dispatcher
        self.jobs = self.fixture.jobs
        self.job = self.fixture.job
        self.handler = self.fixture.handler
        self.handler.return_value = {"status": "ok"}
        self.dispatcher.HANDLERS["learn"] = self.handler

    async def test_lost_success_returns_own_loss_and_never_reads_new_receipt(self):
        self.jobs.mark_done.side_effect = None
        self.jobs.mark_done.return_value = CLAIMS.JobOutcomeReceipt(self.fixture.claim, CLAIMS.OutcomeAck.CLAIM_LOST)
        with self.assertRaises(CLAIMS.JobClaimLostError) as raised:
            await self.dispatcher._execute_claimed(self.job)
        self.assertEqual(raised.exception.job_id, self.job.id)
        self.assertEqual(raised.exception.error_code, "claim_lost")
        self.jobs.get.assert_not_awaited()
        self.jobs.mark_failed.assert_not_awaited()
        self.fixture.notify.assert_not_awaited()
        self.handler.assert_awaited_once()

    async def test_failure_claim_loss_neither_retries_nor_notifies(self):
        self.handler.side_effect = RuntimeError("handler failure")
        self.jobs.mark_failed.side_effect = None
        self.jobs.mark_failed.return_value = CLAIMS.JobOutcomeReceipt(self.fixture.claim, CLAIMS.OutcomeAck.CLAIM_LOST)
        with self.assertRaises(CLAIMS.JobClaimLostError):
            await self.dispatcher._run_claimed_inline(self.job)
        self.jobs.mark_failed.assert_awaited_once()
        self.jobs.mark_done.assert_not_awaited()
        self.fixture.notify.assert_not_awaited()
        self.jobs.get.assert_not_awaited()

    async def test_claim_snapshot_is_not_borrowed_after_handler_changes_observed_generation(self):
        async def mutate(payload):
            self.job.attempts += 1
            self.job.started_at += timedelta(seconds=1)
            return {"status": "ok"}
        self.handler.side_effect = mutate
        await self.dispatcher.execute_job(self.job, self.handler)
        self.assertEqual(self.jobs.mark_done.await_args.kwargs["claim"], self.fixture.claim)

    async def test_invalid_claim_runs_zero_handlers_and_zero_outcome_writes(self):
        self.job.started_at = None
        with self.assertRaises(ValueError):
            await self.dispatcher.execute_job(self.job, self.handler)
        self.handler.assert_not_awaited()
        self.jobs.mark_done.assert_not_awaited()
        self.jobs.mark_failed.assert_not_awaited()

    async def test_false_exact_acquisition_runs_zero_handlers(self):
        self.jobs.claim_job.return_value = None
        self.assertIsNone(await self.dispatcher.run_job_now(7))
        self.handler.assert_not_awaited()
        self.jobs.get.assert_not_awaited()

    async def test_direct_paths_use_acquisition_return_not_bool_then_reload(self):
        self.jobs.enqueue = AsyncMock(return_value=self.job)
        self.jobs.claim_job.return_value = None
        enqueue = AsyncMock(return_value=self.job)
        self.fixture.imports["app.jobs.enqueue"] = fixtures.module_with(enqueue_task_run=enqueue)
        with self.assertRaises(CLAIMS.JobNotAcquiredError):
            await self.dispatcher.run_job_inline("learn")
        with self.assertRaises(CLAIMS.JobNotAcquiredError):
            await self.dispatcher.run_task_directly(SimpleNamespace(tenant_id=31))
        self.assertEqual(self.jobs.claim_job.await_count, 2)
        self.jobs.get.assert_not_awaited()
        self.handler.assert_not_awaited()

    async def test_unknown_handler_uses_claim_bound_receipt_and_actual_retry_status(self):
        self.dispatcher.HANDLERS.clear()
        self.jobs.mark_failed.return_value = CLAIMS.JobOutcomeReceipt(self.fixture.claim, CLAIMS.OutcomeAck.RETRY)
        result = await self.dispatcher._execute_claimed(self.job)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(self.jobs.mark_failed.await_args.kwargs["claim"], self.fixture.claim)
        self.jobs.get.assert_not_awaited()
        self.fixture.notify.assert_not_awaited()

    async def test_wrong_generation_receipt_is_not_accepted_or_reloaded(self):
        self.jobs.mark_done.side_effect = None
        other = replace(self.fixture.claim, attempts=2)
        self.jobs.mark_done.return_value = CLAIMS.JobOutcomeReceipt(other, CLAIMS.OutcomeAck.DONE)
        with self.assertRaises(self.dispatcher.JobOutcomePersistenceError):
            await self.dispatcher._execute_claimed(self.job)
        self.jobs.mark_failed.assert_not_awaited()
        self.jobs.get.assert_not_awaited()
        self.fixture.notify.assert_not_awaited()

    async def test_task_projection_degradation_is_visible_without_retry_or_false_clean_receipt(self):
        self.jobs.mark_done.side_effect = None
        self.jobs.mark_done.return_value = CLAIMS.JobOutcomeReceipt(
            self.fixture.claim, CLAIMS.OutcomeAck.DONE, result={"status": "ok"}, task_projection_failed=True,
        )
        result = await self.dispatcher._execute_claimed(self.job)
        self.assertEqual(result["status"], "done")
        self.assertTrue(result["task_projection_failed"])
        self.jobs.mark_failed.assert_not_awaited()
        self.jobs.get.assert_not_awaited()
        self.fixture.notify.assert_not_awaited()

    async def test_cancellation_is_not_converted_to_claim_loss_or_retry(self):
        self.handler.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.dispatcher.execute_job(self.job, self.handler)
        self.jobs.mark_done.assert_not_awaited()
        self.jobs.mark_failed.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
