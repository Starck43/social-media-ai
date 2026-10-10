"""Run with python tests/test_analyze_error_accounting.py; no app/DB bootstrap."""

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from source_import_isolation import load_isolated_source


class Query:
    def __init__(self, values):
        self.values = values

    def order_by(self, *args):
        return self

    def limit(self, *args):
        return self

    def __await__(self):
        async def resolve():
            return self.values

        return resolve().__await__()


class AnalyzeErrorAccountingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scenario = SimpleNamespace(id=1, is_active=True)
        self.scenario_manager = SimpleNamespace(get_default_scenario=AsyncMock(return_value=self.scenario))
        self.staged = SimpleNamespace(for_source=AsyncMock(return_value=[]))
        self.analytics_manager = SimpleNamespace(filter=Mock(return_value=Query([])))
        models = SimpleNamespace(
            AgentScenario=SimpleNamespace(objects=self.scenario_manager),
            CollectedItem=SimpleNamespace(objects=self.staged),
            AIAnalytics=SimpleNamespace(objects=self.analytics_manager, created_at=SimpleNamespace(desc=lambda: None)),
            BotAction=SimpleNamespace(objects=Mock()),
        )
        self.trigger = SimpleNamespace(should_analyze=AsyncMock(side_effect=lambda content, task: content))
        self.module = load_isolated_source(
            "_analyze_error_accounting",
            Path(__file__).resolve().parents[1] / "app/jobs/handlers.py",
            {
                "app.services.social.credentials": SimpleNamespace(
                    AuthorizationRequired=type("Auth", (Exception,), {})
                ),
                "app.models": models,
                "app.services.ai.trigger_evaluator": SimpleNamespace(trigger_evaluator=self.trigger),
                "app.services.social.guards": SimpleNamespace(extract_target_user=Mock(), guards_checker=Mock()),
                "app.types": SimpleNamespace(BotActionStatus=Mock()),
            },
        )
        self.module.logger = Mock()
        self.module._load_task = AsyncMock(return_value=None)
        self.module._task_payload = Mock(return_value={})
        self.module._resolve_content_window = Mock(return_value={})
        self.module._resolve_action_type = Mock(return_value=None)
        self.source = SimpleNamespace(id=1, name="source", external_id="first", tenant_id=1)
        self.module._resolve_sources = AsyncMock(return_value=[self.source])

    def with_analytics(self):
        row = SimpleNamespace(id=4, summary_data={"summary": "content"}, response_payload={})
        self.analytics_manager.filter.return_value = Query([row])

    async def test_empty_analytics_is_skip_not_error(self):
        result = await self.module.handle_analyze({})
        self.assertEqual((result["skipped"], result["error"], result["staged_errors"]), (1, 0, 0))
        self.assertEqual(result["error_sources"], [])

    async def test_inactive_scenario_is_normal_skip(self):
        self.scenario_manager.get_default_scenario.return_value = None
        result = await self.module.handle_analyze({})
        self.assertEqual((result["skipped"], result["error"]), (1, 0))
        self.staged.for_source.assert_not_awaited()

    async def test_outer_failure_is_error_not_skip(self):
        self.scenario_manager.get_default_scenario.side_effect = RuntimeError("private credentials")
        result = await self.module.handle_analyze({})
        self.assertEqual((result["skipped"], result["error"]), (0, 1))
        self.assertEqual(result["error_sources"], ["source"])
        self.assertTrue(result["per_source"][0]["error"])
        self.assertNotIn("private credentials", repr(result))

    async def test_staged_failure_preserves_existing_analysis_work(self):
        self.with_analytics()
        self.staged.for_source.side_effect = RuntimeError("staging secret")
        result = await self.module.handle_analyze({})
        self.assertEqual((result["analyzed"], result["error"], result["staged_errors"]), (1, 1, 1))
        self.assertTrue(result["per_source"][0]["staged_error"])
        self.assertNotIn("staging secret", repr(result))

    async def test_staged_and_outer_failure_count_source_once(self):
        self.staged.for_source.side_effect = RuntimeError("staging")
        self.analytics_manager.filter.side_effect = RuntimeError("lookup")
        result = await self.module.handle_analyze({})
        self.assertEqual((result["error"], result["staged_errors"], result["skipped"]), (1, 1, 0))
        self.assertEqual(result["error_sources"], ["source"])

    async def test_mixed_sources_continue_after_failure(self):
        self.with_analytics()
        second = SimpleNamespace(id=2, name="second", external_id="second", tenant_id=1)
        self.module._resolve_sources.return_value = [self.source, second]
        self.scenario_manager.get_default_scenario.side_effect = [RuntimeError("failed"), self.scenario]
        result = await self.module.handle_analyze({})
        self.assertEqual((result["sources"], result["analyzed"], result["error"]), (2, 1, 1))
        self.assertEqual(len(result["per_source"]), 2)
        self.assertNotIn("error", result["per_source"][1])

    async def test_clean_success_has_zero_error_counters(self):
        self.with_analytics()
        result = await self.module.handle_analyze({})
        self.assertEqual((result["analyzed"], result["error"], result["staged_errors"]), (1, 0, 0))
        self.assertNotIn("status", result)

    async def test_cancelled_staging_propagates(self):
        self.staged.for_source.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.module.handle_analyze({})
        self.analytics_manager.filter.assert_not_called()


if __name__ == "__main__":
    unittest.main()
