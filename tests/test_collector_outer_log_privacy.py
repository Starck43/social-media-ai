"""Two actual collector outer error sinks; module-local doubles, no bootstrap.

Run: python tests/test_collector_outer_log_privacy.py
Notifications and exception propagation remain existing behavior, not new privacy scope.
"""

import asyncio
import logging
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import test_inline_analysis_errors as inline_fixtures

PRIVATE = "PRIVATE-token-session-provider-url-prompt-sql"


class RecordSink(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


class HostileError(Exception):
    def __str__(self):
        raise AssertionError("Private exception must not be formatted")

    def __repr__(self):
        raise AssertionError("Private exception must not be repr'd")


class HostileId:
    def __str__(self):
        raise AssertionError("Private identifier must not be formatted")

    def __repr__(self):
        raise AssertionError("Private identifier must not be repr'd")


class CollectorOuterLogPrivacyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Fixture setup only; never discover/execute its historical tests.
        f = inline_fixtures.InlineErrorsTests()
        f.setUp()
        self.fixture = f
        self.module = f.module
        self.collector = f.collector
        self.source = f.source
        self.sink = RecordSink()
        self.module.logger = logging.Logger("isolated_collector_outer", logging.DEBUG)
        self.module.logger.addHandler(self.sink)
        self.module.current_tenant_id.return_value = self.source.tenant_id
        self.module.is_bypass.return_value = False
        self.module.tenant_scope.side_effect = lambda tenant_id: nullcontext()
        self.module.messenger_service.send_operator_alert = AsyncMock()
        self.collector.analyzer.base_analyze_content = AsyncMock()

    def collect_failure(self, error):
        # Fail before pre-existing info logs; only the selected catch sink is tested.
        self.source.platform = None
        self.module.Platform.objects.get.side_effect = error

    async def collect(self):
        return await self.collector.collect_from_source(self.source, run_id=42)

    async def analyze(self):
        return await self.collector._analyze_content(
            self.fixture.content, self.source, topic_chain_id="chain", parent_analysis_id=19
        )

    def assert_safe_error(self, event):
        errors = [r for r in self.sink.records if r.levelno >= logging.ERROR]
        self.assertEqual(len(errors), 1)
        record = errors[0]
        self.assertEqual(record.getMessage(), event)
        self.assertEqual(record.args, ())
        self.assertIsNone(record.exc_info)
        self.assertIsNone(record.exc_text)
        self.assertIsNone(record.stack_info)
        self.assertNotIn(PRIVATE, logging.Formatter("%(levelname)s %(message)s").format(record))
        self.assertNotIn(PRIVATE, str(vars(record)))

    async def test_collect_failure_keeps_original_exception_and_notifications(self):
        error = RuntimeError(PRIVATE)
        self.collect_failure(error)
        with self.assertRaises(RuntimeError) as caught:
            await self.collect()
        self.assertIs(caught.exception, error)
        self.assert_safe_error("collection_source_failed error_code=collection_operation_failed")
        self.module.notify.create.assert_awaited_once_with(
            title=f"Ошибка сбора источника {self.source.name}",
            message="Не удалось собрать данные. Проверьте подключение и права доступа к источнику.",
            ntype=self.module.NotificationType.API_ERROR, entity_type="source", entity_id=self.source.id,
            send_to_messenger=False,
        )
        self.module.tenant_scope.assert_called_once_with(self.source.tenant_id)
        self.module.messenger_service.send_operator_alert.assert_awaited_once_with("collection_failed")
        self.fixture.checked.assert_not_awaited()

    async def test_legacy_analysis_failure_keeps_none_and_arguments(self):
        error = ValueError(PRIVATE)
        self.collector.analyzer.base_analyze_content.side_effect = error
        self.assertIsNone(await self.analyze())
        self.assert_safe_error("collection_analysis_failed error_code=analysis_operation_failed")
        self.collector.analyzer.base_analyze_content.assert_awaited_once_with(
            self.fixture.content, self.source, topic_chain_id="chain", parent_analysis_id=19
        )
        self.assertEqual(self.collector.analyzer.reported_errors, 0)
        self.module.notify.create.assert_not_awaited()

    async def test_collect_hostile_exception_is_not_formatted(self):
        error = HostileError(PRIVATE)
        self.collect_failure(error)
        with self.assertRaises(HostileError) as caught:
            await self.collect()
        self.assertIs(caught.exception, error)
        self.assert_safe_error("collection_source_failed error_code=collection_operation_failed")

    async def test_legacy_analysis_hostile_exception_is_not_formatted(self):
        self.collector.analyzer.base_analyze_content.side_effect = HostileError(PRIVATE)
        self.assertIsNone(await self.analyze())
        self.assert_safe_error("collection_analysis_failed error_code=analysis_operation_failed")

    async def test_collect_private_identifier_is_not_formatted_in_catch(self):
        error = RuntimeError(PRIVATE)
        self.source.id = HostileId()
        self.collect_failure(error)
        with self.assertRaises(RuntimeError) as caught:
            await self.collect()
        self.assertIs(caught.exception, error)
        self.assert_safe_error("collection_source_failed error_code=collection_operation_failed")

    async def test_collect_actual_cancellation_propagates_without_alert(self):
        self.collect_failure(asyncio.CancelledError(PRIVATE))
        with self.assertRaises(asyncio.CancelledError):
            await self.collect()
        self.assertEqual(self.sink.records, [])
        self.module.notify.create.assert_not_awaited()
        self.module.messenger_service.send_operator_alert.assert_not_awaited()

    async def test_legacy_analysis_actual_cancellation_propagates(self):
        self.collector.analyzer.base_analyze_content.side_effect = asyncio.CancelledError(PRIVATE)
        with self.assertRaises(asyncio.CancelledError):
            await self.analyze()
        self.assertEqual(self.sink.records, [])

    async def test_collect_success_keeps_staging_retirement_and_diagnostics(self):
        result = await self.collect()
        self.assertEqual((result["content_count"], result["new_items"], result["staged"], result["analytics_count"]), (2, 1, 2, 1))
        self.assertEqual(result["analysis_errors"], 1)
        self.collector._stage_items.assert_awaited_once_with(self.fixture.content, self.source, 42)
        self.collector._retire_staged.assert_awaited_once_with(self.source, self.fixture.fresh)
        self.fixture.checked.assert_awaited_once_with(self.source.id)
        self.assertFalse(any(r.levelno >= logging.WARNING for r in self.sink.records))
        self.module.notify.create.assert_not_awaited()

    async def test_legacy_analysis_success_keeps_none_result(self):
        self.collector.analyzer.base_analyze_content.return_value = SimpleNamespace(id=11)
        self.assertIsNone(await self.analyze())
        self.assertEqual(self.sink.records, [])
        self.assertEqual(self.collector.analyzer.reported_errors, 0)

    async def test_notification_failures_cannot_replace_original_error(self):
        error = RuntimeError(PRIVATE)
        self.collect_failure(error)
        self.module.notify.create.side_effect = ValueError(PRIVATE)
        self.module.messenger_service.send_operator_alert.side_effect = ValueError(PRIVATE)
        with self.assertRaises(RuntimeError) as caught:
            await self.collect()
        self.assertIs(caught.exception, error)
        self.assert_safe_error("collection_source_failed error_code=collection_operation_failed")
        self.assertEqual([r.getMessage() for r in self.sink.records if r.levelno == logging.WARNING],
                         ["Collection workspace notification failed", "Collection operator alert failed"])

    async def test_unauthorized_scope_keeps_notification_skip_and_operator_alert(self):
        error = RuntimeError(PRIVATE)
        self.collect_failure(error)
        self.module.current_tenant_id.return_value = 999
        with self.assertRaises(RuntimeError):
            await self.collect()
        self.module.notify.create.assert_not_awaited()
        self.module.messenger_service.send_operator_alert.assert_awaited_once_with("collection_failed")
        self.assert_safe_error("collection_source_failed error_code=collection_operation_failed")
        self.assertEqual([r.getMessage() for r in self.sink.records if r.levelno == logging.WARNING],
                         ["Collection notification skipped: source workspace unavailable or unauthorized"])

    async def test_notifications_disabled_keeps_original_error_without_alerts(self):
        error = RuntimeError(PRIVATE)
        self.collect_failure(error)
        self.module.NOTIFICATIONS_AVAILABLE = False
        with self.assertRaises(RuntimeError) as caught:
            await self.collect()
        self.assertIs(caught.exception, error)
        self.module.notify.create.assert_not_awaited()
        self.module.messenger_service.send_operator_alert.assert_not_awaited()
        self.assert_safe_error("collection_source_failed error_code=collection_operation_failed")


if __name__ == "__main__":
    unittest.main()
