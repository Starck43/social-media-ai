import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import test_analyzer_reported_errors as _reported_fixtures


class UnifiedAnalyzeHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = _reported_fixtures.StagedSignalTests()
        self.fixture.setUp()
        self.module = self.fixture.module
        self.real_handle_collect = self.module.handle_collect
        self.module._llm_failure_code = AsyncMock(return_value=None)
        self.fixture.source.params = {}
        self.events = []
        scenario = SimpleNamespace(id=11, is_active=True)
        self.task = SimpleNamespace(id=17, agent_scenario=scenario, trigger_config=None)
        self.module._load_task = AsyncMock(return_value=self.task)
        self.module._task_payload = lambda _task: {"collect_before_analyze": True}
        self.collection = {
            "sources": 1,
            "collected": 1,
            "new_items": 2,
            "error": 1,
            "rate_limited": 0,
            "error_sources": ["source"],
            "error_messages": ["private provider error"],
            "per_source": [{"source_id": 1, "name": "source", "outcome": "error", "error": True}],
        }

        async def collect(*args, **kwargs):
            self.events.append("collection")
            return self.collection

        self.module.handle_collect = AsyncMock(side_effect=collect)

        async def analyze(*args, **kwargs):
            self.events.append("analysis")
            return self.fixture.fresh

        self.fixture.analyzer.analyze_content.side_effect = analyze

    async def test_collection_precedes_staged_analysis_and_errors_are_reported(self):
        result = await self.module.handle_analyze({"agent_task_id": self.task.id})

        self.assertEqual(self.events, ["collection", "analysis"])
        self.module.handle_collect.assert_awaited_once_with({"agent_task_id": self.task.id}, stage_only=True)
        self.assertEqual(result["collection_errors"], 1)
        self.assertEqual(result["error"], 1)
        self.assertEqual(result["per_source"][0]["collection"]["outcome"], "error")
        self.assertTrue(result["per_source"][0]["collection_error"])
        self.assertNotIn("private provider error", repr(result))

    async def test_fresh_collection_and_partial_saved_coverage_retire_only_covered_hashes(self):
        covered = "covered-hash"
        uncovered = "uncovered-hash"
        self.fixture.staged = [
            SimpleNamespace(content_hash=covered, as_agent_item=lambda: {"text": "covered", "content_hash": covered}),
            SimpleNamespace(content_hash=uncovered, as_agent_item=lambda: {"text": "uncovered", "content_hash": uncovered}),
        ]
        self.fixture.models.CollectedItem.objects.for_source.return_value = self.fixture.staged
        self.fixture.models.CollectedItem.objects.count_for_source.return_value = 2
        self.fixture.analyzer.completed_hashes = {covered}
        self.fixture.analyzer.current_fingerprint = "fixture-context"
        self.fixture.fresh = [SimpleNamespace(summary_data={"content_hashes": [covered]})]
        self.fixture.analyzer.analyze_content.side_effect = None
        self.fixture.analyzer.analyze_content.return_value = self.fixture.fresh

        result = await self.module.handle_analyze({"agent_task_id": self.task.id})

        self.fixture.analyzer.analyze_content.assert_awaited_once()
        self.fixture.analytics.assert_not_called()
        self.module._retire_staged.assert_awaited_once_with(
            self.fixture.fresh,
            self.fixture.source.id,
            self.fixture.source.tenant_id,
            completed_hashes={covered},
            analysis_fingerprint="fixture-context",
        )
        self.assertEqual(result["per_source"][0]["raw_processed"], 1)
        self.assertEqual(result["per_source"][0]["raw_total"], 2)

    async def test_rate_limited_collection_and_analysis_failure_count_separately(self):
        self.collection = {
            "sources": 1,
            "collected": 0,
            "new_items": 0,
            "error": 0,
            "rate_limited": 1,
            "error_sources": [],
            "per_source": [{"source_id": 1, "name": "source", "outcome": "rate_limited"}],
        }
        self.fixture.analyzer.reported_errors = 1

        result = await self.module.handle_analyze({"agent_task_id": self.task.id})

        self.assertEqual(result["error"], 1)
        self.assertEqual(result["collection_errors"], 0)
        self.assertEqual(result["collection_rate_limited"], 1)
        self.assertEqual(result["staged_errors"], 1)
        self.assertEqual(result["error_sources"], ["source"])

    async def test_no_fresh_analysis_does_not_replay_historical_analytics(self):
        self.fixture.analyzer.analyze_content.side_effect = None
        self.fixture.analyzer.analyze_content.return_value = []
        self.fixture.models.CollectedItem.objects.for_source.return_value = []
        result = await self.module.handle_analyze({"agent_task_id": self.task.id})

        self.assertEqual(result["analyzed"], 0)
        self.fixture.analytics.assert_not_called()

    async def test_stage_only_collection_forces_collector_to_leave_analysis_for_later(self):
        collector = SimpleNamespace(
            collect_from_source=AsyncMock(
                return_value={
                    "content_count": 2,
                    "new_items": 2,
                    "staged": 2,
                    "analytics_count": 0,
                }
            )
        )
        self.module.__isolated_imports__["app.services.monitoring.collector"] = SimpleNamespace(
            ContentCollector=lambda: collector
        )
        self.module.__isolated_imports__["app.services.social.rate_limit"] = SimpleNamespace(
            SourceRateLimited=type("SourceRateLimited", (Exception,), {})
        )
        self.module._load_task = AsyncMock(return_value=self.task)
        self.module._task_payload = lambda _task: {"analyze_inline": True}
        self.module._resolve_content_window = lambda *_args: {}
        self.module._resolve_sources = AsyncMock(return_value=[self.fixture.source])

        result = await self.real_handle_collect({"agent_task_id": self.task.id}, stage_only=True)

        self.assertIs(collector.collect_from_source.await_args.kwargs["analyze"], False)
        self.assertEqual(result["per_source"][0]["staged"], 2)
        self.assertEqual(result["per_source"][0]["analyzed"], 0)
        self.fixture.analyzer.analyze_content.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
