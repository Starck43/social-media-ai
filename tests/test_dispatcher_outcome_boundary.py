"""Prepared standalone regressions for ordinary dispatcher write boundaries.

Owner: python tests/test_dispatcher_outcome_boundary.py
Actual dispatcher source with local infrastructure doubles; no app imports, DB,
provider or live sends. Simulated commits do not prove PostgreSQL atomicity.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock

import test_returned_job_failures as fixtures

PRIVATE = "PRIVATE-SQL-AND-CUSTOMER-VALUE"


class OutcomeBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixture = fixtures.DispatcherTests("test_success_remains_success")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.dispatcher = fixture.dispatcher
        self.job = fixture.job
        self.jobs = fixture.jobs
        self.handler = fixture.handler
        self.handler.return_value = {"status": "ok", "llm_cost": 0.12}
        self.notify = fixture.notify
        self.dispatcher.HANDLERS["learn"] = self.handler

    async def assert_write_error(self, call=None):
        with self.assertLogs(self.dispatcher.logger, level="ERROR") as logs:
            with self.assertRaises(self.dispatcher.JobOutcomePersistenceError) as raised:
                await (call if call is not None else self.dispatcher.execute_job(self.job, self.handler))
        error = raised.exception
        self.assertEqual((error.job_id, error.tenant_id), (7, 31))
        self.assertEqual(error.error_code, "runtime_error")
        self.assertTrue(error.__suppress_context__)
        self.assertNotIn(PRIVATE, str(error))
        self.assertNotIn(PRIVATE, repr(vars(error)))
        self.assertTrue(any("job_outcome_persistence_failed" in record.getMessage() for record in logs.records))
        for record in logs.records:
            self.assertNotIn(PRIVATE, record.getMessage())
            self.assertNotIn(PRIVATE, repr(record.args))
            self.assertIsNone(record.exc_info)
            self.assertIsNone(record.stack_info)
        self.handler.assert_awaited_once()
        return error

    async def commit_done_then_fail(self, job_id, **kwargs):
        self.job.status = "done"
        self.job.result = kwargs["result"]
        raise RuntimeError(PRIVATE)  # Commit may have succeeded, but no receipt was acknowledged.

    async def test_success_write_failure_does_not_attempt_mark_failed_or_notify(self):
        self.jobs.mark_done.side_effect = RuntimeError(PRIVATE)
        await self.assert_write_error()
        self.jobs.mark_done.assert_awaited_once_with(
            7, claim=self.fixture.claim, result=self.handler.return_value, llm_cost=0.12,
        )
        self.jobs.mark_failed.assert_not_awaited()
        self.notify.assert_not_awaited()
        self.assertEqual(self.job.status, "running")  # No success/rollback inferred.

    async def test_post_commit_ack_loss_does_not_reclassify_committed_success(self):
        self.jobs.mark_done.side_effect = self.commit_done_then_fail
        await self.assert_write_error()
        self.assertEqual(self.job.status, "done")
        self.assertIs(self.job.result, self.handler.return_value)
        self.jobs.mark_failed.assert_not_awaited()
        self.notify.assert_not_awaited()

    async def test_declared_failure_write_error_is_not_a_second_failure_write(self):
        self.handler.return_value = {"status": "failed", "error": "invalid_structured_output", "llm_cost": 0}
        self.jobs.mark_failed.side_effect = RuntimeError(PRIVATE)
        await self.assert_write_error()
        self.jobs.mark_failed.assert_awaited_once()
        self.assertFalse(self.jobs.mark_failed.await_args.kwargs["allow_retry"])
        self.assertEqual(self.jobs.mark_failed.await_args.kwargs["llm_cost"], 0.0)
        self.jobs.mark_done.assert_not_awaited()
        self.notify.assert_not_awaited()

    async def test_failed_commit_ack_loss_preserves_original_declared_failure(self):
        self.handler.return_value = {"status": "failed", "error": "invalid_structured_output", "llm_cost": None}

        async def commit_failure(job_id, **kwargs):
            self.job.status = "failed"
            self.job.result = kwargs["result"]
            self.job.error = kwargs["error"]
            raise RuntimeError(PRIVATE)

        self.jobs.mark_failed.side_effect = commit_failure
        await self.assert_write_error()
        self.jobs.mark_failed.assert_awaited_once()
        self.assertEqual(self.job.error, "invalid_structured_output")
        self.assertIsNone(self.job.result["llm_cost"])
        self.notify.assert_not_awaited()

    async def test_skipped_write_failure_does_not_schedule_retry(self):
        self.handler.return_value = {"status": "skipped", "reason": "cost_cap", "llm_cost": 0}
        self.jobs.mark_done.side_effect = RuntimeError(PRIVATE)
        await self.assert_write_error()
        self.assertEqual(self.jobs.mark_done.await_args.kwargs["llm_cost"], 0.0)
        self.jobs.mark_failed.assert_not_awaited()
        self.notify.assert_not_awaited()

    async def test_unknown_cost_not_replaced_by_zero_on_write_error(self):
        self.handler.return_value = {"status": "ok"}
        self.jobs.mark_done.side_effect = RuntimeError(PRIVATE)
        await self.assert_write_error()
        self.assertIsNone(self.jobs.mark_done.await_args.kwargs["llm_cost"])
        self.jobs.mark_failed.assert_not_awaited()

    async def test_unexpected_notification_error_cannot_rewrite_committed_job(self):
        async def commit_done(job_id, **kwargs):
            self.job.status = "done"
            self.job.result = kwargs["result"]
            return fixtures.CLAIMS.JobOutcomeReceipt(
                kwargs["claim"], fixtures.CLAIMS.OutcomeAck.DONE, result=kwargs["result"],
            )

        self.jobs.mark_done.side_effect = commit_done
        self.notify.side_effect = RuntimeError(PRIVATE)
        with self.assertLogs(self.dispatcher.logger, level="ERROR") as logs:
            receipt = await self.dispatcher.execute_job(self.job, self.handler)
        self.assertEqual(receipt.acknowledgement, fixtures.CLAIMS.OutcomeAck.DONE)
        self.assertTrue(receipt.notification_projection_failed)
        self.assertNotIn(PRIVATE, str(logs.output))
        self.assertEqual(self.job.status, "done")
        self.notify.assert_awaited_once()
        self.jobs.mark_failed.assert_not_awaited()

    async def test_handler_exception_failure_write_error_is_reported_without_second_write(self):
        self.handler.side_effect = ValueError("original handler failure")
        self.jobs.mark_failed.side_effect = RuntimeError(PRIVATE)
        await self.assert_write_error()
        self.jobs.mark_failed.assert_awaited_once_with(
            7, claim=self.fixture.claim, error="original handler failure", allow_retry=True,
        )
        self.jobs.mark_done.assert_not_awaited()
        self.notify.assert_not_awaited()

    async def test_inline_failure_does_not_reload_current_job_as_a_success_receipt(self):
        self.jobs.mark_done.side_effect = self.commit_done_then_fail
        await self.assert_write_error(self.dispatcher._run_claimed_inline(self.job))
        self.jobs.get.assert_not_awaited()
        self.jobs.mark_failed.assert_not_awaited()

    async def test_operator_failure_propagates_without_current_row_reload(self):
        self.jobs.mark_done.side_effect = self.commit_done_then_fail
        await self.assert_write_error(self.dispatcher.run_job_now(7, allow_retry=False))
        self.jobs.claim_job.assert_awaited_once_with(7)
        self.jobs.get.assert_not_awaited()
        self.jobs.mark_failed.assert_not_awaited()

    async def test_worker_iteration_failure_propagates_without_extra_finalization(self):
        self.jobs.claim_next = AsyncMock(return_value=self.job)
        self.jobs.reap_stale = AsyncMock(return_value=0)
        self.jobs.cleanup_done = AsyncMock(return_value=0)
        self.jobs.mark_done.side_effect = RuntimeError(PRIVATE)
        await self.assert_write_error(self.dispatcher.run_pending_once())
        self.jobs.claim_next.assert_awaited_once()
        self.jobs.mark_failed.assert_not_awaited()

    async def test_write_cancellation_propagates_without_retry(self):
        self.jobs.mark_done.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.dispatcher.execute_job(self.job, self.handler)
        self.jobs.mark_failed.assert_not_awaited()
        self.notify.assert_not_awaited()

    async def test_legacy_digest_without_checkpoint_uses_ordinary_error_boundary(self):
        self.job.job_type = "digest"
        self.fixture.digest_execute.return_value = {"status": "ok", "llm_cost": 0.12}
        self.jobs.mark_done.side_effect = RuntimeError(PRIVATE)
        with self.assertRaises(self.dispatcher.JobOutcomePersistenceError):
            await self.dispatcher.execute_job(self.job, self.handler)
        self.fixture.digest_execute.assert_awaited_once()
        self.fixture.finalize.assert_not_awaited()
        self.jobs.mark_failed.assert_not_awaited()

    async def test_diagnostic_path_does_not_stringify_exception(self):
        class UnsafeException(Exception):
            def __str__(self):
                raise AssertionError("Must not stringify persistence errors")

        self.jobs.mark_done.side_effect = UnsafeException()
        with self.assertRaises(self.dispatcher.JobOutcomePersistenceError) as raised:
            await self.dispatcher.execute_job(self.job, self.handler)
        self.assertEqual(raised.exception.error_code, "unexpected_error")
        self.jobs.mark_failed.assert_not_awaited()
        self.notify.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
