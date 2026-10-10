"""Standalone policy/mocked-source tests; no application, DB or live sends.

Run: python tests/test_returned_job_failures.py.
Actual dispatcher/manager source is loaded with infrastructure imports mocked.
This is not PostgreSQL, lease/concurrency or full-suite acceptance.
"""

import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]


def load_source(name, path, imports=None):
    return load_isolated_source(name, ROOT / path, imports=imports)


OUTCOMES = load_source("_returned_outcomes_test", "app/jobs/result_outcomes.py")


def module_with(**values):
    module = types.ModuleType("mock_infrastructure")
    module.__dict__.update(values)
    return module


class PolicyTests(unittest.TestCase):
    def test_explicit_failure(self):
        failure = OUTCOMES.returned_failure({"status": "failed", "error": "invalid_structured_output", "llm_cost": 0.2})
        self.assertEqual(failure.audit_result(), {"status": "failed", "error": "invalid_structured_output", "llm_cost": 0.2})

    def test_raw_provider_text_and_extra_payload_not_audited(self):
        result = {"status": "failed", "error": "PRIVATE-PROVIDER-TEXT", "response": {"customer": "PRIVATE"}}
        self.assertNotIn("PRIVATE", str(OUTCOMES.returned_failure(result).audit_result()))

    def test_unknown_error_shapes_use_static_code(self):
        for error in (None, {}, [], True, "other_code"):
            self.assertEqual(OUTCOMES.returned_failure({"status": "failed", "error": error}).code, "handler_returned_failure")

    def test_legacy_error_counters_are_not_terminal_failure(self):
        for value in ({"error": 2}, {"status": "ok"}, {"status": "skipped"}, None, []):
            self.assertIsNone(OUTCOMES.returned_failure(value))

    def test_cost_preserves_reported_zero(self):
        for cost in (0, 0.0, 0.12):
            self.assertEqual(OUTCOMES.reported_llm_cost({"llm_cost": cost}), float(cost))

    def test_invalid_cost_is_unknown_not_coerced(self):
        for cost in (None, True, "0.2", -1, float("nan"), float("inf"), 10 ** 1000):
            self.assertIsNone(OUTCOMES.reported_llm_cost({"llm_cost": cost}))


class DispatcherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scopes = []

        @contextmanager
        def scope(tenant_id=None, **kwargs):
            self.scopes.append(tenant_id)
            yield

        self.job = SimpleNamespace(id=7, tenant_id=31, agent_task_id=9, job_type="learn", payload={},
                                   result=None, status="running", error=None)
        self.jobs = SimpleNamespace(mark_done=AsyncMock(), mark_failed=AsyncMock(return_value=False),
                                    get=AsyncMock(return_value=self.job), claim_job=AsyncMock(return_value=self.job))
        self.finalize = AsyncMock(return_value=False)
        self.digest_execute = AsyncMock()
        self.flag = False

        class DeliveryFailure(RuntimeError):
            def __init__(self, code, retryable=False):
                super().__init__(code)
                self.retryable = retryable

        self.DeliveryFailure = DeliveryFailure
        modules = {
            "app.core.tenant_context": module_with(tenant_scope=scope),
            "app.jobs.handlers": module_with(HANDLERS={}),
            "app.jobs.result_outcomes": OUTCOMES,
            "app.models.managers.job_manager": module_with(JobManager=lambda: self.jobs),
            "app.services.digest.delivery_outcomes": module_with(DeliveryFailure=DeliveryFailure),
            "app.services.digest.job_delivery": module_with(
                REFERENCE_KEY="digest_delivery", enabled=lambda: self.flag,
                execute_digest_job=self.digest_execute, finalize_digest_job=self.finalize),
        }
        # Late imports stay local to this actual-source module as well.
        self.imports = modules
        self.dispatcher = load_source("_dispatcher_failure_test", "app/jobs/dispatcher.py", imports=modules)
        self.notify = self.dispatcher._notify_job_result = AsyncMock()
        self.handler = AsyncMock(return_value={"status": "failed", "error": "invalid_structured_output", "llm_cost": 0.12})

    async def test_failed_result_not_done_not_retried_and_retains_cost(self):
        await self.dispatcher.execute_job(self.job, self.handler)
        self.jobs.mark_done.assert_not_awaited()
        self.jobs.mark_failed.assert_awaited_once_with(
            7, error="invalid_structured_output", allow_retry=False,
            result={"status": "failed", "error": "invalid_structured_output", "llm_cost": 0.12}, llm_cost=0.12)
        self.assertFalse(self.notify.await_args.kwargs["success"])

    async def test_reflect_failure_uses_same_terminal_path(self):
        self.job.job_type = "reflect"
        await self.dispatcher.execute_job(self.job, self.handler)
        self.assertFalse(self.jobs.mark_failed.await_args.kwargs["allow_retry"])
        self.jobs.mark_done.assert_not_awaited()

    async def test_unknown_usage_is_not_recorded_as_zero(self):
        self.handler.return_value = {"status": "failed", "error": "llm_call_failed", "llm_cost": None}
        await self.dispatcher.execute_job(self.job, self.handler)
        self.assertIsNone(self.jobs.mark_failed.await_args.kwargs["llm_cost"])

    async def test_zero_cost_is_explicit_on_failure(self):
        self.handler.return_value["llm_cost"] = 0
        await self.dispatcher.execute_job(self.job, self.handler)
        self.assertEqual(self.jobs.mark_failed.await_args.kwargs["llm_cost"], 0.0)

    async def test_failure_text_not_in_logs_or_notification_or_audit(self):
        self.handler.return_value = {"status": "failed", "error": "PRIVATE-TEXT", "response": "PRIVATE"}
        with self.assertLogs(self.dispatcher.logger, level="ERROR") as logs:
            await self.dispatcher.execute_job(self.job, self.handler)
        self.assertNotIn("PRIVATE", str(logs.output) + str(self.notify.await_args) + str(self.jobs.mark_failed.await_args))

    async def test_result_persistence_failure_does_not_enable_retry(self):
        self.jobs.mark_failed.side_effect = [RuntimeError("write_failed"), False]
        with self.assertRaises(self.dispatcher.JobOutcomePersistenceError):
            await self.dispatcher.execute_job(self.job, self.handler)
        self.jobs.mark_failed.assert_awaited_once()
        self.assertFalse(self.jobs.mark_failed.await_args.kwargs["allow_retry"])
        self.notify.assert_not_awaited()
        self.jobs.mark_done.assert_not_awaited()

    async def test_normal_exception_backoff_is_preserved(self):
        self.handler.side_effect = RuntimeError("transport_failed")
        self.jobs.mark_failed.return_value = True
        await self.dispatcher.execute_job(self.job, self.handler)
        self.jobs.mark_failed.assert_awaited_once_with(7, error="transport_failed", allow_retry=True)
        self.notify.assert_not_awaited()

    async def test_inline_exception_remains_terminal(self):
        self.handler.side_effect = RuntimeError("transport_failed")
        await self.dispatcher.execute_job(self.job, self.handler, allow_retry=False)
        self.assertFalse(self.jobs.mark_failed.await_args.kwargs["allow_retry"])

    async def test_collection_error_counter_keeps_legacy_result(self):
        self.job.job_type = "collect"
        self.handler.return_value = {"collected": 1, "error": 1, "items": 3}
        await self.dispatcher.execute_job(self.job, self.handler)
        self.jobs.mark_done.assert_awaited_once_with(7, result=self.handler.return_value, llm_cost=None)
        self.jobs.mark_failed.assert_not_awaited()

    async def test_skipped_has_no_success_notification_and_retains_zero(self):
        self.handler.return_value = {"status": "skipped", "reason": "cost_cap", "llm_cost": 0}
        await self.dispatcher.execute_job(self.job, self.handler)
        self.assertEqual(self.jobs.mark_done.await_args.kwargs["llm_cost"], 0.0)
        self.notify.assert_not_awaited()

    async def test_success_remains_success(self):
        self.handler.return_value = {"status": "ok", "llm_cost": 0.3}
        await self.dispatcher.execute_job(self.job, self.handler)
        self.assertTrue(self.notify.await_args.kwargs["success"])
        self.assertEqual(self.jobs.mark_done.await_args.kwargs["llm_cost"], 0.3)

    async def test_checkpoint_failure_uses_existing_finalizer_not_generic_path(self):
        self.job.job_type = "digest"
        self.flag = True
        self.digest_execute.return_value = {"status": "failed"}
        self.finalize.side_effect = [self.DeliveryFailure("delivery_uncertain"), False]
        await self.dispatcher.execute_job(self.job, self.handler)
        self.assertEqual(self.finalize.await_count, 2)
        self.assertFalse(self.finalize.await_args.kwargs["allow_retry"])
        self.jobs.mark_failed.assert_not_awaited()
        self.jobs.mark_done.assert_not_awaited()

    async def test_original_checkpoint_reference_routes_after_flag_off(self):
        self.job.job_type = "digest"
        self.job.result = {"digest_delivery": {"run_id": 3}}
        self.digest_execute.return_value = {"status": "sent"}
        await self.dispatcher.execute_job(self.job, self.handler)
        self.finalize.assert_awaited_once()
        self.jobs.mark_done.assert_not_awaited()

    async def test_inline_returned_failure_is_visible_in_final_row(self):
        async def fail(job_id, **kwargs):
            self.job.status = "failed"
            self.job.error = kwargs["error"]
            self.job.result = kwargs["result"]
            return False
        self.jobs.mark_failed.side_effect = fail
        self.dispatcher.HANDLERS["learn"] = self.handler
        result = await self.dispatcher._run_claimed_inline(self.job)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "invalid_structured_output")

    async def test_payload_identity_comes_from_claimed_columns(self):
        self.job.payload = {"agent_task_id": 999, "job_id": 999}
        await self.dispatcher.execute_job(self.job, self.handler)
        self.assertEqual(self.handler.await_args.args[0], {"agent_task_id": 9, "job_id": 7})
        self.assertEqual(self.scopes, [31])


class ManagerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        class BaseManager:
            def __class_getitem__(cls, item):
                return cls
        modules = {
            "app.models.managers.base_manager": module_with(BaseManager=BaseManager),
            "app.core.config": module_with(settings=SimpleNamespace(JOB_RETRY_BACKOFF_SECONDS=300)),
        }
        module = load_source(
            "app.models.managers._failure_manager_test", "app/models/managers/job_manager.py", imports=modules,
        )
        self.manager = module.JobManager.__new__(module.JobManager)
        self.job = SimpleNamespace(id=7, attempts=1, max_attempts=3)
        self.manager.get = AsyncMock(return_value=self.job)
        self.manager.update_by_id = AsyncMock()
        self.manager._record_task_result = AsyncMock()

    async def test_terminal_failure_metadata_written_with_status(self):
        result = {"status": "failed", "error": "invalid_structured_output", "llm_cost": 0.12}
        retried = await self.manager.mark_failed(7, "invalid_structured_output", False, result=result, llm_cost=0.12)
        self.assertFalse(retried)
        update = self.manager.update_by_id.await_args.kwargs
        self.assertEqual((update["status"], update["result"], update["llm_cost"]), ("failed", result, 0.12))
        self.assertIn("finished_at", update)
        self.manager._record_task_result.assert_awaited_once_with(self.job, status="failed", error="invalid_structured_output")

    async def test_zero_cost_written_and_unknown_not_invented(self):
        await self.manager.mark_failed(7, "failed", False, llm_cost=0)
        self.assertEqual(self.manager.update_by_id.await_args.kwargs["llm_cost"], 0.0)
        await self.manager.mark_failed(7, "failed", False, llm_cost=None)
        self.assertNotIn("llm_cost", self.manager.update_by_id.await_args.kwargs)

    async def test_legacy_exception_retry_keeps_backoff(self):
        self.assertTrue(await self.manager.mark_failed(7, "failed"))
        update = self.manager.update_by_id.await_args.kwargs
        self.assertEqual(update["status"], "pending")
        self.assertIn("run_at", update)
        self.assertNotIn("result", update)
        self.manager._record_task_result.assert_not_awaited()

    async def test_missing_row_does_not_write(self):
        self.manager.get.return_value = None
        self.assertFalse(await self.manager.mark_failed(7, "failed", False))
        self.manager.update_by_id.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
