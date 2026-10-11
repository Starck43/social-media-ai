"""Actual collector stage/retire source; module-local doubles, no DB/bootstrap.

Run: python tests/test_collector_staging_log_privacy.py
Only the two swallowed storage-exception warning sinks are covered.
"""

import asyncio
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = "PRIVATE-token-session-sql-content-hash-url"


class RecordSink(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


class Transaction:
    def __init__(self):
        self.enter = AsyncMock()
        self.exit = AsyncMock(return_value=False)

    async def __aenter__(self):
        await self.enter()
        return self

    async def __aexit__(self, *args):
        return await self.exit(*args)


class HostileValue:
    def __str__(self):
        raise AssertionError("Private values must not be formatted")

    def __repr__(self):
        raise AssertionError("Private values must not be repr'd")


class HostileError(Exception):
    def __str__(self):
        raise AssertionError("Storage exception must not be formatted")

    def __repr__(self):
        raise AssertionError("Storage exception must not be repr'd")


class CollectorStagingLogPrivacyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ns = SimpleNamespace
        self.transaction = Transaction()
        self.session = ns(begin=Mock(return_value=self.transaction), close=AsyncMock())
        self.new_session = Mock(return_value=self.session)
        self.store = AsyncMock(return_value=3)
        self.delete = AsyncMock(return_value=2)
        self.hashes = [PRIVATE]
        self.analysed_hashes = Mock(return_value=self.hashes)
        imports = {
            "app.core.tenant_context": ns(current_tenant_id=Mock(), is_bypass=Mock(), tenant_scope=Mock()),
            "app.models": ns(Platform=Mock(), Source=Mock(), CollectedItem=ns(
                objects=ns(store_items=self.store, delete_hashes=self.delete))),
            "app.services.ai.analyzer": ns(AIAnalyzer=Mock()),
            "app.services.social.factory": ns(get_social_client=Mock()),
            "app.types": ns(NotificationType=Mock(), SourceType=Mock()),
            "app.services.notifications.messenger": ns(messenger_service=Mock()),
            "app.services.notifications.service": ns(notify=Mock()),
            "app.core.database": ns(new_session=self.new_session),
            "app.services.ai.dedup": ns(item_hash=Mock(return_value=PRIVATE), analysed_hashes=self.analysed_hashes),
            "app.utils.date_parsing": ns(universal_date_parser=Mock(return_value="parsed-date")),
        }
        imports["app.utils.content_attachments"] = load_isolated_source(
            "_collector_snapshot_fixture", ROOT / "app/utils/content_attachments.py")
        imports["app.utils.collected_content"] = load_isolated_source(
            "_collector_row_fixture", ROOT / "app/utils/collected_content.py", imports)
        self.module = load_isolated_source("_collector_staging_privacy", ROOT / "app/services/monitoring/collector.py", imports)
        self.sink = RecordSink()
        self.module.logger = logging.Logger("isolated_collector_staging", logging.DEBUG)
        self.module.logger.addHandler(self.sink)
        self.collector = self.module.ContentCollector()
        self.source = ns(id=17, tenant_id=4)
        self.items = [{"text": PRIVATE, "url": PRIVATE, "external_id": "item", "published_at": "raw-date",
                       "metrics": {"likes": 3}, "views": 8, "author": {"id": "author"}, "media_type": "text"}]
        self.analytics = [ns(summary_data={"content_hashes": self.hashes})]

    async def invoke(self, stage):
        if stage == "stage":
            return await self.collector._stage_items(self.items, self.source, 29)
        return await self.collector._retire_staged(self.source, self.analytics)

    def assert_safe_warning(self, stage):
        warnings = [r for r in self.sink.records if r.levelno >= logging.WARNING]
        self.assertEqual(len(warnings), 1)
        record = warnings[0]
        event = "collector_staging_failed" if stage == "stage" else "collector_retirement_failed"
        self.assertEqual(record.levelno, logging.WARNING)
        self.assertEqual(record.getMessage(), event + " error_code=storage_operation_failed")
        self.assertEqual(record.args, ())
        self.assertIsNone(record.exc_info)
        self.assertIsNone(record.exc_text)
        self.assertIsNone(record.stack_info)
        self.assertNotIn(PRIVATE, logging.Formatter("%(levelname)s %(message)s").format(record))
        self.assertNotIn(PRIVATE, str(vars(record)))

    async def test_stage_failure_keeps_zero_and_close(self):
        error = RuntimeError(PRIVATE)
        self.store.side_effect = error
        self.assertEqual(await self.invoke("stage"), 0)
        self.assert_safe_warning("stage")
        self.session.close.assert_awaited_once_with()
        self.transaction.exit.assert_awaited_once_with(type(error), error, unittest.mock.ANY)

    async def test_retire_failure_keeps_zero_hash_selection_and_close(self):
        self.delete.side_effect = RuntimeError(PRIVATE)
        self.assertEqual(await self.invoke("retire"), 0)
        self.assert_safe_warning("retire")
        self.analysed_hashes.assert_called_once_with(self.analytics)
        self.delete.assert_awaited_once_with(self.session, 17, self.hashes)
        self.session.close.assert_awaited_once_with()

    async def test_hostile_exception_is_not_formatted(self):
        for stage in ("stage", "retire"):
            with self.subTest(stage=stage):
                self.setUp()
                operation = self.store if stage == "stage" else self.delete
                operation.side_effect = HostileError(PRIVATE)
                self.assertEqual(await self.invoke(stage), 0)
                self.assert_safe_warning(stage)
                self.session.close.assert_awaited_once()

    async def test_private_source_identifier_is_not_formatted_on_failure(self):
        for stage in ("stage", "retire"):
            with self.subTest(stage=stage):
                self.setUp()
                self.source.id = HostileValue()
                operation = self.store if stage == "stage" else self.delete
                operation.side_effect = RuntimeError(PRIVATE)
                self.assertEqual(await self.invoke(stage), 0)
                self.assert_safe_warning(stage)

    async def test_begin_failure_keeps_warning_fallback_and_close(self):
        for stage in ("stage", "retire"):
            with self.subTest(stage=stage):
                self.setUp()
                self.transaction.enter.side_effect = OSError(PRIVATE)
                self.assertEqual(await self.invoke(stage), 0)
                self.assert_safe_warning(stage)
                self.store.assert_not_awaited()
                self.delete.assert_not_awaited()
                self.session.close.assert_awaited_once()

    async def test_exit_failure_keeps_warning_fallback_and_close(self):
        for stage in ("stage", "retire"):
            with self.subTest(stage=stage):
                self.setUp()
                self.transaction.exit.side_effect = ValueError(PRIVATE)
                self.assertEqual(await self.invoke(stage), 0)
                self.assert_safe_warning(stage)
                self.session.close.assert_awaited_once()

    async def cancellation(self, stage):
        operation = self.store if stage == "stage" else self.delete
        operation.side_effect = asyncio.CancelledError(PRIVATE)
        with self.assertRaises(asyncio.CancelledError):
            await self.invoke(stage)
        self.session.close.assert_awaited_once()
        self.assertFalse(any(r.levelno >= logging.WARNING for r in self.sink.records))

    async def test_stage_cancellation_propagates_and_closes(self):
        await self.cancellation("stage")

    async def test_retire_cancellation_propagates_and_closes(self):
        await self.cancellation("retire")

    async def test_stage_success_keeps_exact_rows_and_return(self):
        self.assertEqual(await self.invoke("stage"), 3)
        self.store.assert_awaited_once_with(self.session, [{
            "run_id": 29, "source_id": 17, "external_id": "item", "content_hash": PRIVATE,
            "platform": None, "published_at": "parsed-date", "media_type": "text", "text": PRIVATE,
            "metrics": {"likes": 3, "views": 8}, "author": {"id": "author"}, "permalink": PRIVATE,
        }])
        self.assertEqual(self.items[0]["permalink"], PRIVATE)
        self.session.close.assert_awaited_once()
        self.assertFalse(any(r.levelno >= logging.WARNING for r in self.sink.records))

    async def test_retire_success_keeps_exact_hashes_and_return(self):
        self.assertEqual(await self.invoke("retire"), 2)
        self.delete.assert_awaited_once_with(self.session, 17, self.hashes)
        self.session.close.assert_awaited_once()
        self.assertFalse(any(r.levelno >= logging.WARNING for r in self.sink.records))

    async def test_retire_without_saved_hashes_never_opens_session(self):
        self.analysed_hashes.return_value = []
        self.assertEqual(await self.invoke("retire"), 0)
        self.new_session.assert_not_called()
        self.delete.assert_not_awaited()
        self.assertEqual(self.sink.records, [])

    async def test_close_failure_still_propagates(self):
        for stage in ("stage", "retire"):
            with self.subTest(stage=stage):
                self.setUp()
                self.session.close.side_effect = RuntimeError(PRIVATE)
                with self.assertRaises(RuntimeError):
                    await self.invoke(stage)
                self.assertFalse(any(r.levelno >= logging.WARNING for r in self.sink.records))

    async def test_preparation_failure_remains_outside_storage_catch(self):
        self.module.__isolated_imports__["app.services.ai.dedup"].item_hash.side_effect = ValueError(PRIVATE)
        with self.assertRaises(ValueError):
            await self.invoke("stage")
        self.new_session.assert_not_called()
        self.assertEqual(self.sink.records, [])


if __name__ == "__main__":
    unittest.main()
