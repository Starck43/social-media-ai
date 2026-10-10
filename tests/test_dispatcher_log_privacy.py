"""Actual-source dispatcher privacy tests with mocked infrastructure.

Run directly without pytest/DB. Not a fleet-wide log or PostgreSQL audit.
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import test_returned_job_failures as fixtures

SECRET = "PRIVATE-CUSTOMER-AND-FAKE-TOKEN"


class DispatcherLogPrivacyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Reuse the existing actual-source fixture, without collecting its tests twice.
        fixture = fixtures.DispatcherTests("test_success_remains_success")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.dispatcher = fixtures.load_source(
            "_dispatcher_log_privacy_test", "app/jobs/dispatcher.py", imports=fixture.imports,
        )
        self.original_notify = self.dispatcher._notify_job_result
        self.dispatcher._notify_job_result = fixture.notify
        self.job = fixture.job
        self.jobs = fixture.jobs
        self.handler = fixture.handler
        self.notify = fixture.notify

    def assert_private_logs(self, logs, event):
        self.assertTrue(any(event in line for line in logs.output), logs.output)
        for record in logs.records:
            self.assertNotIn(SECRET, record.getMessage())
            self.assertNotIn(SECRET, repr(record.args))
            self.assertIsNone(record.exc_info)
            self.assertIsNone(record.exc_text)
            self.assertIsNone(record.stack_info)

    async def test_success_result_and_payload_not_logged_but_audit_unchanged(self):
        self.job.payload = {"prompt": SECRET}
        result = {"status": "ok", "response": SECRET, "llm_cost": 0.12}
        self.handler.return_value = result
        with self.assertLogs(self.dispatcher.logger, level="INFO") as logs:
            await self.dispatcher.execute_job(self.job, self.handler)
        self.assert_private_logs(logs, "job_done")
        self.assertIn("job_id=7 tenant_id=31 task_id=9 job_type=learn", logs.output[0])
        self.jobs.mark_done.assert_awaited_once_with(7, claim=self.fixture.claim, result=result, llm_cost=0.12)
        self.assertIs(self.notify.await_args.kwargs["result"], result)

    async def test_retry_exception_not_logged_and_retry_policy_unchanged(self):
        self.handler.side_effect = RuntimeError(SECRET)
        self.jobs.mark_failed.return_value = fixtures.CLAIMS.JobOutcomeReceipt(
            self.fixture.claim, fixtures.CLAIMS.OutcomeAck.RETRY)
        with self.assertLogs(self.dispatcher.logger, level="WARNING") as logs:
            await self.dispatcher.execute_job(self.job, self.handler)
        self.assert_private_logs(logs, "job_retry_scheduled")
        self.assertIn("error_code=runtime_error", logs.output[0])
        self.jobs.mark_failed.assert_awaited_once_with(7, claim=self.fixture.claim, error=SECRET, allow_retry=True)
        self.notify.assert_not_awaited()

    async def test_terminal_exception_not_logged_or_passed_to_failure_notification(self):
        self.handler.side_effect = TimeoutError(SECRET)
        with self.assertLogs(self.dispatcher.logger, level="ERROR") as logs:
            await self.dispatcher.execute_job(self.job, self.handler, allow_retry=False)
        self.assert_private_logs(logs, "job_failed_terminal")
        self.assertIn("error_code=timeout", logs.output[0])
        self.jobs.mark_failed.assert_awaited_once_with(7, claim=self.fixture.claim, error=SECRET, allow_retry=False)
        self.assertEqual(self.notify.await_args.kwargs["error"], self.dispatcher._FAILURE_MESSAGE)

    async def test_returned_failure_retains_safe_code_and_unknown_cost(self):
        self.handler.return_value = {"status": "failed", "error": "invalid_structured_output", "response": SECRET}
        with self.assertLogs(self.dispatcher.logger, level="ERROR") as logs:
            await self.dispatcher.execute_job(self.job, self.handler)
        self.assert_private_logs(logs, "job_returned_failure")
        self.assertIn("error_code=invalid_structured_output", logs.output[0])
        self.assertIsNone(self.jobs.mark_failed.await_args.kwargs["llm_cost"])

    async def test_persistence_exception_preserves_terminal_declared_failure(self):
        self.jobs.mark_failed.side_effect = [RuntimeError(SECRET), False]
        with self.assertLogs(self.dispatcher.logger, level="ERROR") as logs:
            with self.assertRaises(self.dispatcher.JobOutcomePersistenceError) as raised:
                await self.dispatcher.execute_job(self.job, self.handler)
        self.assert_private_logs(logs, "job_outcome_persistence_failed")
        self.assertNotIn(SECRET, str(raised.exception))
        self.jobs.mark_failed.assert_awaited_once()
        self.assertFalse(self.jobs.mark_failed.await_args.kwargs["allow_retry"])
        self.notify.assert_not_awaited()
        self.jobs.mark_done.assert_not_awaited()

    async def test_skipped_result_not_logged_or_notified(self):
        self.handler.return_value = {"status": "skipped", "reason": SECRET, "llm_cost": 0}
        with self.assertLogs(self.dispatcher.logger, level="INFO") as logs:
            await self.dispatcher.execute_job(self.job, self.handler)
        self.assert_private_logs(logs, "job_done")
        self.notify.assert_not_awaited()
        self.assertEqual(self.jobs.mark_done.await_args.kwargs["llm_cost"], 0.0)

    async def test_claim_loss_logs_safe_metadata_and_does_not_notify(self):
        self.job.job_type = "digest"
        self.fixture.flag = True
        self.fixture.digest_execute.return_value = {"status": "sent", "response": SECRET}
        self.fixture.finalize.return_value = None
        with self.assertLogs(self.dispatcher.logger, level="WARNING") as logs:
            await self.dispatcher.execute_job(self.job, self.handler)
        self.assert_private_logs(logs, "job_completion_claim_lost")
        self.notify.assert_not_awaited()
        self.jobs.mark_done.assert_not_awaited()

    async def test_checkpoint_failure_keeps_finalizer_and_retry_refusal(self):
        self.job.job_type = "digest"
        self.fixture.flag = True
        self.fixture.finalize.side_effect = [self.fixture.DeliveryFailure(SECRET), False]
        with self.assertLogs(self.dispatcher.logger, level="ERROR") as logs:
            await self.dispatcher.execute_job(self.job, self.handler)
        self.assert_private_logs(logs, "job_failed_terminal")
        self.assertFalse(self.fixture.finalize.await_args.kwargs["allow_retry"])
        self.jobs.mark_failed.assert_not_awaited()

    def test_error_categories_never_stringify_messages_or_custom_class_names(self):
        class DangerousError(Exception):
            def __str__(self):
                raise AssertionError("Must not stringify exception for logging")
        DangerousError.__name__ = SECRET
        cases = [(TimeoutError(SECRET), "timeout"), (ConnectionError(SECRET), "connection_error"),
                 (OSError(SECRET), "io_error"), (ValueError(SECRET), "value_error"),
                 (RuntimeError(SECRET), "runtime_error"), (DangerousError(), "unexpected_error")]
        for error, expected in cases:
            self.assertEqual(self.dispatcher._error_kind(error), expected)

    def test_unexpected_identifier_and_type_values_not_formatted(self):
        class DangerousValue:
            def __str__(self):
                raise AssertionError("Must not stringify unexpected identifiers")
        job = SimpleNamespace(id=DangerousValue(), tenant_id=SECRET, agent_task_id=True, job_type=SECRET)
        with self.assertLogs(self.dispatcher.logger, level="INFO") as logs:
            self.dispatcher._log_job(20, "job_done", job)
        self.assert_private_logs(logs, "job_done")
        self.assertIn("job_id=None tenant_id=None task_id=None job_type=unknown", logs.output[0])

    async def test_failure_notification_ignores_supplied_raw_error_and_bad_type(self):
        notify = SimpleNamespace(create=AsyncMock())
        modules = {"app.services.notifications.service": fixtures.module_with(notify=notify),
                   "app.types": fixtures.module_with(NotificationType=SimpleNamespace(API_ERROR="error", REPORT_READY="ready"))}
        self.job.job_type = SECRET
        with patch.dict(self.dispatcher.__isolated_imports__, modules):
            await self.original_notify(self.job, success=False, error=SECRET)
        self.assertNotIn(SECRET, str(notify.create.await_args))
        self.assertIn(self.dispatcher._FAILURE_MESSAGE, notify.create.await_args.kwargs["message"])
        self.assertEqual(notify.create.await_args.kwargs["entity_id"], 9)

    async def test_notification_write_errors_not_logged_and_are_swallowed(self):
        notify = SimpleNamespace(create=AsyncMock(side_effect=RuntimeError(SECRET)))
        modules = {"app.services.notifications.service": fixtures.module_with(notify=notify),
                   "app.types": fixtures.module_with(NotificationType=SimpleNamespace(API_ERROR="error", REPORT_READY="ready"))}
        with patch.dict(self.dispatcher.__isolated_imports__, modules):
            for success, event in ((False, "job_error_notification_failed"), (True, "job_success_notification_failed")):
                with self.assertLogs(self.dispatcher.logger, level="ERROR") as logs:
                    await self.original_notify(self.job, success=success, result={"text": SECRET})
                self.assert_private_logs(logs, event)
        self.assertEqual(notify.create.await_count, 2)

    async def test_worker_failure_log_safe_and_cancellation_stops_loop(self):
        self.dispatcher.drain = AsyncMock(side_effect=[RuntimeError(SECRET), asyncio.CancelledError()])
        with patch.object(self.dispatcher.asyncio, "sleep", AsyncMock()):
            with self.assertLogs(self.dispatcher.logger, level="INFO") as logs:
                await self.dispatcher.worker_forever()
        self.assert_private_logs(logs, "worker_iteration_failed")
        self.assertIn("worker_stopped", logs.output[-1])
        self.assertEqual(self.dispatcher.drain.await_count, 2)


if __name__ == "__main__":
    unittest.main()
