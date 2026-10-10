"""Run directly: actual collector/handle_collect source, no DB/network/bootstrap.

python tests/test_inline_analysis_errors.py
These query/provider/storage doubles do not establish live acceptance.
"""

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = "PRIVATE-ANALYZER-DETAIL"


class AuthorizationRequired(Exception):
    pass


class InlineErrorsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user_type = SimpleNamespace(name="USER")
        self.source = self.make_source(1)
        self.children = [self.make_source(i) for i in (7, 8)]
        self.content = [{"text": PRIVATE}, {"text": "second"}]
        self.client = SimpleNamespace(collect_data=AsyncMock(return_value=self.content))
        self.fresh = [SimpleNamespace(id=3)]
        self.after = 1
        self.analyzer = SimpleNamespace(reported_errors=0, analyze_content=AsyncMock(side_effect=self.analyze))
        self.filter = Mock(return_value=SimpleNamespace(first=AsyncMock()))
        self.checked = AsyncMock()
        self.models = SimpleNamespace(
            Platform=SimpleNamespace(objects=SimpleNamespace(get=AsyncMock())),
            Source=SimpleNamespace(objects=SimpleNamespace(filter=self.filter, update_last_checked=self.checked)),
        )
        imports = {
            "app.core.tenant_context": SimpleNamespace(
                current_tenant_id=Mock(), is_bypass=Mock(), tenant_scope=Mock()
            ),
            "app.models": self.models,
            "app.services.ai.analyzer": SimpleNamespace(AIAnalyzer=Mock(return_value=self.analyzer)),
            "app.services.social.factory": SimpleNamespace(get_social_client=Mock(return_value=self.client)),
            "app.types": SimpleNamespace(SourceType=SimpleNamespace(USER=self.user_type), NotificationType=Mock()),
            "app.services.notifications.messenger": SimpleNamespace(messenger_service=Mock()),
            "app.services.notifications.service": SimpleNamespace(notify=SimpleNamespace(create=AsyncMock())),
            "app.services.social.credentials": SimpleNamespace(AuthorizationRequired=AuthorizationRequired),
            "app.services.ai.dedup": SimpleNamespace(item_hash=Mock(return_value="measured-hash")),
        }
        self.module = load_isolated_source("_inline_collector", ROOT / "app/services/monitoring/collector.py", imports)
        self.module.logger = Mock()
        self.collector = self.module.ContentCollector()
        self.collector._count_new_items = AsyncMock(return_value=1)
        self.collector._stage_items = AsyncMock(return_value=2)
        self.collector._retire_staged = AsyncMock(return_value=2)
        self.handlers = load_isolated_source(
            "_inline_collect_handler", ROOT / "app/jobs/handlers.py",
            {
                "app.services.social.credentials": imports["app.services.social.credentials"],
                "app.services.monitoring.collector": SimpleNamespace(ContentCollector=lambda: self.collector),
            },
        )
        self.handlers.logger = Mock()
        self.handlers._load_task = AsyncMock(return_value=None)
        self.task_payload = {}
        self.handlers._task_payload = Mock(side_effect=lambda task: self.task_payload)
        self.handlers._resolve_sources = AsyncMock(return_value=[self.source])
        self.handlers._resolve_content_window = Mock(return_value={})

    def make_source(self, source_id):
        return SimpleNamespace(
            id=source_id, name=f"source{source_id}", external_id=f"source{source_id}",
            platform_id=3, platform=SimpleNamespace(params={}), source_type=self.user_type,
            last_checked=None, params={}, tenant_id=1,
        )

    async def analyze(self, *args, **kwargs):
        self.analyzer.reported_errors = self.after
        return self.fresh

    async def collect(self, **kwargs):
        return await self.collector.collect_from_source(self.source, **kwargs)

    async def handle(self, **payload):
        return await self.handlers.handle_collect({"analyze_inline": True, "job_id": 42, **payload})

    def assert_collected(self, result):
        self.assertEqual((result["collected"], result["items"], result["new_items"], result["empty"]), (1, 2, 1, 0))
        self.assertEqual(result["per_source"][0]["outcome"], "collected")
        self.assertNotIn("status", result)
        self.assertNotIn(PRIVATE, repr(result))

    def monitored(self):
        self.task_payload = {"monitored_users": ["a", "b"]}
        self.filter.return_value.first.side_effect = self.children

    async def test_partial_analysis_preserves_collected_rows_and_stage_order(self):
        events = []
        async def stage(*args):
            events.append("stage")
            return 2
        async def analyze(*args, **kwargs):
            events.append("analyze")
            return await self.analyze(*args, **kwargs)
        async def retire(*args):
            events.append("retire")
            return 2
        self.collector._stage_items.side_effect = stage
        self.analyzer.analyze_content.side_effect = analyze
        self.collector._retire_staged.side_effect = retire
        result = await self.collect(run_id=42)
        self.assertEqual(events, ["stage", "analyze", "retire"])
        self.assertEqual((result["content_count"], result["new_items"], result["analytics_count"]), (2, 1, 1))
        self.assertEqual(result["analysis_errors"], 1)
        self.collector._retire_staged.assert_awaited_once_with(self.source, self.fresh)
        self.checked.assert_awaited_once_with(1)
        self.assertNotIn(PRIVATE, repr(result))

    async def test_delta_does_not_include_old_source_errors(self):
        self.analyzer.reported_errors = 4
        self.after = 6
        self.assertEqual((await self.collect())["analysis_errors"], 2)

    async def test_clean_call_after_failure_reports_measured_zero(self):
        self.assertEqual((await self.collect())["analysis_errors"], 1)
        self.after = 1
        self.assertEqual((await self.collect())["analysis_errors"], 0)

    async def test_deferred_analysis_omits_unmeasured_diagnostic(self):
        result = await self.collect(analyze=False)
        self.assertNotIn("analysis_errors", result)
        self.assertEqual((result["analytics_count"], result["staged"]), (0, 2))
        self.analyzer.analyze_content.assert_not_awaited()
        self.collector._retire_staged.assert_not_awaited()

    async def test_missing_counter_stays_unknown(self):
        del self.analyzer.reported_errors
        self.assertNotIn("analysis_errors", await self.collect())

    async def test_malformed_before_counter_not_coerced(self):
        for value in (True, False, 1.0, "0", None, -1):
            with self.subTest(value=value):
                self.analyzer.reported_errors = value
                self.assertNotIn("analysis_errors", await self.collect())

    async def test_malformed_after_counter_not_coerced(self):
        for value in (True, False, 1.0, "1", None, -1):
            with self.subTest(value=value):
                self.analyzer.reported_errors = 0
                self.after = value
                self.assertNotIn("analysis_errors", await self.collect())

    async def test_counter_reset_is_unknown_not_negative_or_old_failure(self):
        self.analyzer.reported_errors = 4
        self.after = 1
        self.assertNotIn("analysis_errors", await self.collect())

    async def test_empty_collection_keeps_none_contract(self):
        self.client.collect_data.return_value = []
        self.assertIsNone(await self.collect())
        self.analyzer.analyze_content.assert_not_awaited()
        self.collector._stage_items.assert_not_awaited()

    async def test_empty_analysis_still_reports_explicit_failure(self):
        self.fresh = []
        result = await self.collect()
        self.assertEqual((result["analysis_errors"], result["analytics_count"]), (1, 0))
        self.assertEqual(result["content_count"], 2)
        self.collector._retire_staged.assert_awaited_once_with(self.source, [])

    async def test_cancelled_analysis_propagates_without_retirement(self):
        self.analyzer.analyze_content.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.collect()
        self.collector._retire_staged.assert_not_awaited()
        self.checked.assert_not_awaited()

    async def test_direct_handler_keeps_success_and_marks_source_once(self):
        result = await self.handle()
        self.assert_collected(result)
        self.assertEqual((result["error"], result["error_sources"]), (1, ["source1"]))
        self.assertEqual(result["per_source"][0]["analysis_errors"], 1)
        self.assertTrue(result["per_source"][0]["error"])
        self.assertEqual(result["error_messages"], [])

    async def test_second_clean_source_does_not_inherit_first_error(self):
        self.handlers._resolve_sources.return_value = [self.source, self.children[0]]
        result = await self.handle()
        self.assertEqual((result["sources"], result["collected"], result["error"]), (2, 2, 1))
        self.assertTrue(result["per_source"][0]["error"])
        self.assertNotIn("error", result["per_source"][1])

    async def test_legacy_direct_handler_has_no_new_marker(self):
        self.collector.collect_from_source = AsyncMock(return_value={"content_count": 2, "new_items": 1})
        result = await self.handle()
        self.assert_collected(result)
        self.assertEqual(result["error"], 0)
        self.assertNotIn("analysis_errors", result["per_source"][0])

    async def test_handler_rejects_malformed_reported_counts(self):
        for value in (True, False, 1.0, "1", None, -1, 0):
            with self.subTest(value=value):
                self.collector.collect_from_source = AsyncMock(return_value={
                    "content_count": 2, "new_items": 1, "analysis_errors": value,
                })
                result = await self.handle()
                self.assertEqual(result["error"], 0)
                self.assertNotIn("analysis_errors", result["per_source"][0])

    async def test_real_monitored_children_aggregate_without_losing_success(self):
        self.monitored()
        result = await self.handle()
        self.assertEqual((result["collected"], result["items"], result["new_items"], result["error"]), (1, 4, 2, 1))
        self.assertEqual(result["per_source"][0]["analysis_errors"], 1)
        self.assertNotIn("monitored_errors", result["per_source"][0])
        self.assertEqual(self.analyzer.analyze_content.await_count, 2)

    async def test_child_analysis_errors_do_not_become_collection_failures(self):
        self.monitored()
        result = await self.collector.collect_monitored_users(self.source, monitored_users=["a", "b"])
        self.assertEqual((result["successful"], result["failed"], result["total_items"]), (2, 0, 4))
        self.assertEqual(result["analysis_errors"], 1)

    async def test_collection_and_analysis_failures_count_parent_once(self):
        self.monitored()
        self.collector.collect_from_source = AsyncMock(side_effect=[
            {"content_count": 2, "new_items": 1, "analysis_errors": 3}, AuthorizationRequired(PRIVATE),
        ])
        result = await self.handle()
        self.assert_collected(result)
        self.assertEqual(result["error"], 1)
        per = result["per_source"][0]
        self.assertEqual((per["analysis_errors"], per["monitored_errors"]), (3, 1))
        self.assertTrue(per["auth_required"])
        self.assertNotIn(PRIVATE, repr(result))

    async def test_monitored_positive_aggregate_is_reported_not_complete_total(self):
        self.monitored()
        self.collector.collect_from_source = AsyncMock(side_effect=[
            {"content_count": 2, "new_items": 1, "analysis_errors": 2},
            {"content_count": 2, "new_items": 1},
        ])
        result = await self.collector.collect_monitored_users(self.source, monitored_users=["a", "b"])
        self.assertEqual(result["analysis_errors"], 2)
        self.assertEqual((result["successful"], result["failed"]), (2, 0))

    async def test_monitored_unknown_or_malformed_not_zero(self):
        for value in (True, False, 1.0, "1", None, -1, 0):
            with self.subTest(value=value):
                self.filter.return_value.first.side_effect = [self.children[0]]
                self.collector.collect_from_source = AsyncMock(return_value={
                    "content_count": 2, "new_items": 1, "analysis_errors": value,
                })
                result = await self.collector.collect_monitored_users(self.source, monitored_users=["a"])
                self.assertNotIn("analysis_errors", result)
                self.assertEqual(result["failed"], 0)

    async def test_all_reported_child_errors_keep_successful_totals(self):
        self.monitored()
        self.collector.collect_from_source = AsyncMock(side_effect=[
            {"content_count": 2, "new_items": 1, "analysis_errors": 2},
            {"content_count": 3, "new_items": 0, "analysis_errors": 4},
        ])
        result = await self.handle()
        self.assertEqual((result["items"], result["new_items"], result["error"]), (5, 1, 1))
        self.assertEqual(result["per_source"][0]["analysis_errors"], 6)
        self.assertEqual(result["per_source"][0]["outcome"], "collected")

    async def test_cancelled_monitored_child_propagates(self):
        self.monitored()
        self.analyzer.analyze_content.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.handle()
        self.assertEqual(self.analyzer.analyze_content.await_count, 1)
        self.collector._retire_staged.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
