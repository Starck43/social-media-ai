"""Actual ingest module with local doubles; no app/bootstrap/DB/network imports.

Run: python tests/test_ingest_log_privacy.py
Only this new suite is executed; no historical fixture or test discovery.
"""
import ast
import asyncio
import logging
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import AsyncMock, patch

SOURCE = Path(__file__).resolve().parents[1] / "app/services/monitoring/ingest.py"
PRIVATE = "PRIVATE-token-chat-post-source-analysis"


class HostileValue:
    def __str__(self):
        raise AssertionError("Identifier must not be formatted")

    def __repr__(self):
        raise AssertionError("Identifier must not be repr'd")


class Sink(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def stub(name, **attributes):
    module = ModuleType(name)
    module.__dict__.update(attributes)
    return module


class IngestLogPrivacyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.trace = []

        @contextmanager
        def tenant_scope(tenant_id=None, *, bypass=False):
            self.trace.append(("tenant_enter", tenant_id, bypass))
            try:
                yield
            finally:
                self.trace.append(("tenant_exit", tenant_id, bypass))

        @contextmanager
        def permission_scope(entity, action):
            self.trace.append(("permission_enter", entity, action))
            try:
                yield
            finally:
                self.trace.append(("permission_exit", entity, action))

        self.source = SimpleNamespace(id=PRIVATE, tenant_id=17, last_item_id=None)
        self.analytics = SimpleNamespace(id=HostileValue())
        self.source_query = SimpleNamespace(get=AsyncMock(return_value=self.source))
        self.sources = SimpleNamespace(
            select_related=lambda *a: self.source_query,
            update_by_id=AsyncMock(), update_last_checked=AsyncMock(),
            filter=lambda **kw: SimpleNamespace(first=AsyncMock(return_value=self.source)),
        )
        self.platforms = SimpleNamespace(filter=lambda **kw: SimpleNamespace(
            first=AsyncMock(return_value=SimpleNamespace(id=3))))
        self.channels = SimpleNamespace(filter=lambda **kw: SimpleNamespace(
            first=AsyncMock(return_value=None)))
        self.analyzer = SimpleNamespace(base_analyze_content=AsyncMock(return_value=self.analytics))
        self.modules = {name: stub(name) for name in (
            "app", "app.core", "app.services", "app.services.ai")}
        self.modules.update({
            "app.core.permissions": stub("app.core.permissions", service_permission_scope=permission_scope),
            "app.core.tenant_context": stub("app.core.tenant_context", tenant_scope=tenant_scope),
            "app.models": stub("app.models", Source=SimpleNamespace(objects=self.sources),
                               Platform=SimpleNamespace(objects=self.platforms),
                               TenantChannel=SimpleNamespace(objects=self.channels)),
            "app.services.ai.analyzer": stub("app.services.ai.analyzer", AIAnalyzer=lambda: self.analyzer),
        })
        self.imports = patch.dict(sys.modules, self.modules)
        self.imports.start()
        self.addCleanup(self.imports.stop)
        self.module = stub("isolated_ingest")
        exec(compile(SOURCE.read_text(), str(SOURCE), "exec"), self.module.__dict__)
        self.actual_find = self.module._find_source
        self.actual_digest = self.module._is_digest_target
        self.module._find_source = AsyncMock(return_value=(PRIVATE, 17, None))
        self.module._is_digest_target = AsyncMock(return_value=False)
        self.sink = Sink()
        self.module.logger = logging.Logger("isolated_ingest", logging.DEBUG)
        self.module.logger.addHandler(self.sink)
        self.inbound = SimpleNamespace(is_channel_post=True, chat_id=PRIVATE, channel="telegram",
            raw={"channel_post": {"message_id": PRIVATE, "text": PRIVATE, "date": 1}})

    async def ingest(self):
        return await self.module.ingest_channel_post(self.inbound)

    def safe_events(self, *events):
        self.assertEqual([r.getMessage() for r in self.sink.records], list(events))
        for record in self.sink.records:
            self.assertEqual(record.args, ())
            self.assertIsNone(record.exc_info)
            self.assertIsNone(record.exc_text)
            self.assertIsNone(record.stack_info)
            self.assertNotIn(PRIVATE, str(vars(record)))
            self.assertNotIn(PRIVATE, logging.Formatter().format(record))

    def no_writes(self):
        self.sources.update_by_id.assert_not_awaited()
        self.sources.update_last_checked.assert_not_awaited()

    async def test_no_source_static_debug(self):
        self.module._find_source.return_value = None
        self.assertFalse(await self.ingest())
        self.safe_events("ingest_source_missing")
        self.assertEqual(self.sink.records[0].levelno, logging.DEBUG)
        self.module._is_digest_target.assert_not_awaited()
        self.no_writes()

    async def test_digest_target_static_debug_no_watermark_advance(self):
        self.module._is_digest_target.return_value = True
        self.assertFalse(await self.ingest())
        self.safe_events("ingest_digest_target_skipped")
        self.module._is_digest_target.assert_awaited_once_with(17, "telegram", PRIVATE)
        self.analyzer.base_analyze_content.assert_not_awaited()
        self.no_writes()

    async def test_watermark_static_debug_no_analysis(self):
        self.inbound.raw["channel_post"]["message_id"] = "41"
        self.module._find_source.return_value = (PRIVATE, 17, "42")
        self.assertFalse(await self.ingest())
        self.safe_events("ingest_watermark_skipped")
        self.analyzer.base_analyze_content.assert_not_awaited()
        self.no_writes()

    async def test_none_analysis_static_warning_no_writes(self):
        self.analyzer.base_analyze_content.return_value = None
        self.assertFalse(await self.ingest())
        self.safe_events("ingest_analysis_failed error_code=analysis_result_missing")
        self.assertEqual(self.sink.records[0].levelno, logging.WARNING)
        self.no_writes()
        self.assertEqual(self.trace, [("tenant_enter", 17, False), ("tenant_exit", 17, False)])

    async def test_success_exact_content_writes_and_scopes(self):
        expected = self.module.normalize_channel_post(self.inbound)
        self.assertTrue(await self.ingest())
        self.safe_events("ingest_stored")
        self.assertEqual(self.sink.records[0].levelno, logging.INFO)
        self.analyzer.base_analyze_content.assert_awaited_once_with([expected], self.source)
        self.source_query.get.assert_awaited_once_with(id=PRIVATE)
        self.sources.update_by_id.assert_awaited_once_with(PRIVATE, last_item_id=PRIVATE)
        self.sources.update_last_checked.assert_awaited_once_with(PRIVATE)
        self.assertEqual(self.trace, [("tenant_enter", 17, False),
            ("permission_enter", "source", "update"), ("permission_exit", "source", "update"),
            ("tenant_exit", 17, False)])

    async def test_missing_resolved_source_still_silent(self):
        self.source_query.get.return_value = None
        self.assertFalse(await self.ingest())
        self.safe_events()
        self.analyzer.base_analyze_content.assert_not_awaited()
        self.no_writes()

    async def test_non_channel_still_silent(self):
        self.inbound.is_channel_post = False
        self.assertFalse(await self.ingest())
        self.module._find_source.assert_not_awaited()
        self.safe_events()

    async def test_empty_text_still_silent(self):
        self.inbound.raw["channel_post"]["text"] = ""
        self.assertFalse(await self.ingest())
        self.module._find_source.assert_not_awaited()
        self.safe_events()

    async def test_hostile_chat_not_formatted_at_missing_source(self):
        self.inbound.chat_id = HostileValue()
        self.module.normalize_channel_post = lambda inbound: {"id": "1"}
        self.module._find_source.return_value = None
        self.assertFalse(await self.ingest())
        self.safe_events("ingest_source_missing")

    async def test_hostile_source_not_formatted_at_analysis_failure(self):
        self.source.id = HostileValue()
        self.analyzer.base_analyze_content.return_value = None
        self.assertFalse(await self.ingest())
        self.safe_events("ingest_analysis_failed error_code=analysis_result_missing")
        self.no_writes()

    async def test_errors_preserve_identity_and_do_not_log_success(self):
        for target in (self.module._find_source, self.module._is_digest_target,
                       self.source_query.get, self.analyzer.base_analyze_content,
                       self.sources.update_by_id, self.sources.update_last_checked):
            with self.subTest(target=target):
                error = RuntimeError(PRIVATE)
                target.side_effect = error
                with self.assertRaises(RuntimeError) as caught:
                    await self.ingest()
                self.assertIs(caught.exception, error)
                target.side_effect = None
                self.safe_events()

    async def test_actual_cancellation_propagates_at_every_await(self):
        for target in (self.module._find_source, self.module._is_digest_target,
                       self.source_query.get, self.analyzer.base_analyze_content,
                       self.sources.update_by_id, self.sources.update_last_checked):
            with self.subTest(target=target):
                error = asyncio.CancelledError()
                target.side_effect = error
                with self.assertRaises(asyncio.CancelledError) as caught:
                    await self.ingest()
                self.assertIs(caught.exception, error)
                target.side_effect = None
                self.safe_events()

    async def test_actual_find_source_keeps_bypass_and_selection(self):
        self.assertEqual(await self.actual_find(PRIVATE), (PRIVATE, 17, None))
        self.assertEqual(self.trace, [("tenant_enter", None, True), ("tenant_exit", None, True)])

    async def test_actual_digest_lookup_keeps_bypass(self):
        self.assertFalse(await self.actual_digest(17, "telegram", PRIVATE))
        self.assertEqual(self.trace, [("tenant_enter", None, True), ("tenant_exit", None, True)])

    def test_watermark_and_normalization_unchanged(self):
        item = self.module.normalize_channel_post(self.inbound)
        self.assertEqual(item["text"], PRIVATE)
        self.assertEqual(item["external_id"], f"{PRIVATE}_{PRIVATE}")
        self.assertFalse(self.module._is_watermark_passed(item, "not-an-id"))
        self.assertTrue(self.module._is_watermark_passed({"id": "42"}, "42"))
        self.assertFalse(self.module._is_watermark_passed({"id": "43"}, "42"))

    def test_all_five_log_sinks_have_only_static_messages(self):
        tree = ast.parse(SOURCE.read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "logger"]
        self.assertEqual(len(calls), 5)
        for call in calls:
            self.assertEqual(len(call.args), 1)
            self.assertIsInstance(call.args[0], ast.Constant)
            self.assertIsInstance(call.args[0].value, str)
            self.assertEqual(call.keywords, [])


if __name__ == "__main__":
    unittest.main()
