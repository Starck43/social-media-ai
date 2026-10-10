"""Standalone dispatcher notification checks; no DB/app bootstrap or live sends.

Run: python tests/test_source_error_notifications.py.
Actual source, local infrastructure doubles; not DB/delivery acceptance.
"""

import asyncio
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import test_returned_job_failures as fixtures

PRIVATE = "PRIVATE-PROVIDER-EXCEPTION"


class SourceErrorNotificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixture = fixtures.DispatcherTests("test_success_remains_success")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.job = fixture.job
        self.job.job_type = "collect"
        self.jobs = fixture.jobs
        self.handler = fixture.handler
        self.active_scopes = []
        self.notification_scopes = []

        @contextmanager
        def scope(tenant_id):
            self.active_scopes.append(tenant_id)
            try:
                yield
            finally:
                self.active_scopes.pop()

        async def create(**kwargs):
            self.notification_scopes.append(list(self.active_scopes))

        self.create = AsyncMock(side_effect=create)
        imports = dict(fixture.imports)
        imports.update(
            {
                "app.core.tenant_context": SimpleNamespace(tenant_scope=scope),
                "app.services.notifications.service": SimpleNamespace(notify=SimpleNamespace(create=self.create)),
                "app.types": SimpleNamespace(
                    NotificationType=SimpleNamespace(API_ERROR="API_ERROR", REPORT_READY="REPORT_READY")
                ),
            }
        )
        self.dispatcher = fixtures.load_source("_source_error_notifications", "app/jobs/dispatcher.py", imports)

    async def execute(self, result, job_type="collect"):
        self.job.job_type = job_type
        self.handler.return_value = result
        return await self.dispatcher.execute_job(self.job, self.handler)

    def note(self):
        self.create.assert_awaited_once()
        return self.create.await_args.kwargs

    def assert_warning(self):
        note = self.note()
        self.assertEqual(note["ntype"], "API_ERROR")
        self.assertIn("с ошибками", note["title"])
        self.assertIn("неполными", note["message"])
        self.assertNotIn("успешно завершена", note["message"])
        self.assertNotIn("не выполнена", note["message"])
        self.assertNotIn(PRIVATE, repr(note))
        self.assertEqual((note["entity_type"], note["entity_id"]), ("task", 9))
        return note

    async def test_mixed_collect_warns_and_preserves_committed_result_and_cost(self):
        result = {
            "sources": 2,
            "collected": 1,
            "items": 32,
            "new_items": 5,
            "error": 1,
            "collected_sources": ["good"],
            "error_sources": ["unavailable"],
            "error_messages": [PRIVATE],
            "per_source": [{"error": PRIVATE}],
            "llm_cost": 0,
        }
        receipt = await self.execute(result)
        note = self.assert_warning()
        self.assertIn("Получено от источников: 32", note["message"])
        self.assertIn("новых, ранее не виденных: 5", note["message"])
        self.assertEqual(receipt.acknowledgement, fixtures.CLAIMS.OutcomeAck.DONE)
        self.assertIs(receipt.result, result)
        self.jobs.mark_done.assert_awaited_once_with(
            7, claim=fixtures.CLAIMS.JobClaim.capture(self.job), result=result, llm_cost=0.0
        )
        self.jobs.mark_failed.assert_not_awaited()
        self.assertEqual(self.notification_scopes, [[31]])
        self.assertEqual(self.active_scopes, [])
        self.assertNotIn("send_to_messenger", note)

    async def test_all_collect_sources_failed_is_not_empty_success(self):
        await self.execute({"sources": 2, "collected": 0, "error": 2})
        note = self.assert_warning()
        self.assertIn("Ошибки при сборе: 2", note["message"])
        self.assertNotIn("Новых данных не получено", note["message"])
        self.assertNotIn("Новых нет", note["message"])

    async def test_clean_collect_retains_received_vs_new_and_empty_contract(self):
        await self.execute({"collected": 1, "items": 32, "new_items": 0, "error": 0})
        note = self.note()
        self.assertEqual(note["ntype"], "REPORT_READY")
        self.assertIn("успешно завершена", note["message"])
        self.assertIn("Получено от источников: 32", note["message"])
        self.assertIn("Новых нет", note["message"])

    async def test_legacy_collect_does_not_invent_measured_new_items(self):
        await self.execute({"collected": 1, "items": 12})
        self.assertIn("не подсчитано", self.note()["message"])
        self.assertNotIn("Новых нет", self.note()["message"])

    async def test_empty_and_excluded_collect_are_not_failures(self):
        await self.execute({"collected": 0, "error": 0, "empty": 1, "excluded": 1})
        note = self.note()
        self.assertEqual(note["ntype"], "REPORT_READY")
        self.assertIn("Без контента: 1", note["message"])
        self.assertIn("исключено из мониторинга", note["message"])

    async def test_analyze_error_uses_bounded_summary_not_raw_result(self):
        result = {
            "error": 1,
            "staged_errors": 0,
            "analyzed": 3,
            "actions_created": 2,
            "skipped": 1,
            "error_sources": [PRIVATE],
            "per_source": [{"error": PRIVATE}],
            "response": PRIVATE,
        }
        await self.execute(result, "analyze")
        note = self.assert_warning()
        self.assertIn("Источников с ошибками: 1", note["message"])
        self.assertIn("Записей прошло фильтр анализа: 3", note["message"])
        self.assertIn("Действий подготовлено: 2", note["message"])
        self.assertIn("Пропущено: 1", note["message"])
        self.assertNotIn("Источников обработано", note["message"])

    async def test_staged_errors_warn_even_without_aggregate_error(self):
        await self.execute({"error": 0, "staged_errors": 1, "analyzed": 4}, "analyze")
        note = self.assert_warning()
        self.assertIn("отложенных данных: 1", note["message"])
        self.assertIn("анализа: 4", note["message"])

    async def test_staged_and_outer_errors_are_not_added_together(self):
        await self.execute({"error": 2, "staged_errors": 1}, "analyze")
        self.assertIn("Источников с ошибками: 2", self.assert_warning()["message"])
        self.assertNotIn("Источников с ошибками: 3", self.note()["message"])

    async def test_clean_analyze_with_normal_skips_is_report_ready(self):
        await self.execute({"error": 0, "staged_errors": 0, "skipped": 2, "analyzed": 0}, "analyze")
        note = self.note()
        self.assertEqual(note["ntype"], "REPORT_READY")
        self.assertIn("успешно завершена", note["message"])
        self.assertIn("Пропущено: 2", note["message"])

    async def test_legacy_analyze_admits_unknown_error_accounting(self):
        await self.execute({"analyzed": 3, "skipped": 2, "per_source": [{"name": PRIVATE}]}, "analyze")
        note = self.note()
        self.assertEqual(note["ntype"], "REPORT_READY")
        self.assertIn("Сведения об ошибках источников не учтены", note["message"])
        self.assertNotIn("успешно завершена", note["message"])
        self.assertNotIn(PRIVATE, note["message"])
        self.assertNotIn("Действий подготовлено: 0", note["message"])

    async def test_malformed_counters_are_not_coerced_or_echoed(self):
        for invalid in (True, False, "2", PRIVATE, -1, [], {}, None, 1.5):
            with self.subTest(invalid=invalid):
                self.create.reset_mock()
                await self.execute({"error": invalid, "staged_errors": invalid, "analyzed": invalid}, "analyze")
                note = self.note()
                self.assertEqual(note["ntype"], "REPORT_READY")
                self.assertIn("не учтены", note["message"])
                self.assertNotIn("успешно завершена", note["message"])
                self.assertNotIn("фильтр анализа:", note["message"])
                self.assertNotIn(PRIVATE, repr(note))

    async def test_other_jobs_are_not_reclassified_by_source_counters(self):
        await self.execute({"error": 2, "staged_errors": 1}, "learn")
        self.assertEqual(self.note()["ntype"], "REPORT_READY")

    async def test_explicit_terminal_failure_retains_fixed_failure_template(self):
        await self.execute({"status": "failed", "error": PRIVATE}, "analyze")
        note = self.note()
        self.assertEqual(note["ntype"], "API_ERROR")
        self.assertIn("не выполнена", note["message"])
        self.assertNotIn(PRIVATE, repr(note))
        self.jobs.mark_done.assert_not_awaited()
        self.assertFalse(self.jobs.mark_failed.await_args.kwargs["allow_retry"])

    async def test_scheduled_skip_is_still_silent(self):
        await self.execute({"status": "skipped", "error": 1}, "analyze")
        self.create.assert_not_awaited()

    async def test_unacknowledged_or_degraded_outcomes_do_not_notify(self):
        claim = fixtures.CLAIMS.JobClaim.capture(self.job)
        for ack, degraded in (
            (fixtures.CLAIMS.OutcomeAck.CLAIM_LOST, False),
            (fixtures.CLAIMS.OutcomeAck.RETRY, False),
            (fixtures.CLAIMS.OutcomeAck.DONE, True),
        ):
            with self.subTest(ack=ack, degraded=degraded):
                receipt = fixtures.CLAIMS.JobOutcomeReceipt(
                    claim, ack, result={"error": 1}, task_projection_failed=degraded
                )
                await self.dispatcher._post_ordinary_outcome(receipt)
                self.create.assert_not_awaited()

    async def test_notification_write_failure_does_not_rewrite_job_or_retry(self):
        self.create.side_effect = RuntimeError(PRIVATE)
        with self.assertLogs(self.dispatcher.logger, level="ERROR") as logs:
            receipt = await self.execute({"error": 1})
        self.assertEqual(receipt.acknowledgement, fixtures.CLAIMS.OutcomeAck.DONE)
        self.jobs.mark_done.assert_awaited_once()
        self.jobs.mark_failed.assert_not_awaited()
        self.create.assert_awaited_once()
        self.assertNotIn(PRIVATE, str(logs.output))

    async def test_cancelled_notification_propagates_without_failure_write(self):
        self.create.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.execute({"error": 1})
        self.jobs.mark_done.assert_awaited_once()
        self.jobs.mark_failed.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
