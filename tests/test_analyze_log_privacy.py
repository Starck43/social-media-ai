"""Actual-source analyze exception-log privacy tests with mocked infrastructure.

Run directly without pytest/DB. Not a fleet-wide log or PostgreSQL audit.
"""

import logging
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

from source_import_isolation import load_isolated_source

SECRET = "PRIVATE-CUSTOMER-TOKEN-AND-SQL"


def module_with(**values):
    module = ModuleType("mock_infrastructure")
    module.__dict__.update(values)
    return module


class QueryDouble:
    """Awaitable query chain double returning a fixed result."""

    def __init__(self, result):
        self._result = result

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def __await__(self):
        async def _coro():
            return self._result

        return _coro().__await__()


class RaisingQueryDouble:
    """Awaitable query chain double that raises when awaited."""

    def __init__(self, error):
        self._error = error

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def __await__(self):
        async def _coro():
            raise self._error

        return _coro().__await__()


class AnalyzeLogPrivacyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scenario = SimpleNamespace(id=5, is_active=True)
        self.source = SimpleNamespace(id=23, name=SECRET, external_id="", tenant_id=1, params=None)
        self.for_source = AsyncMock(return_value=[])
        self.analytics_query = lambda: QueryDouble([])
        self.should_analyze = AsyncMock(return_value=[])
        self.analyze_content = AsyncMock()
        self.retire_staged = AsyncMock(return_value=1)
        self.count_failed = AsyncMock(return_value=0)
        self.get_default_scenario = AsyncMock(return_value=self.scenario)
        self.tenant_scope_calls = []
        self.source_filter = AsyncMock(return_value=[self.source])

        from contextlib import contextmanager

        @contextmanager
        def scope(tenant_id=None, **kwargs):
            self.tenant_scope_calls.append(tenant_id)
            yield

        self.scope = scope
        modules = {
            "app.services.social.credentials": module_with(AuthorizationRequired=RuntimeError),
            "app.core.tenant_context": module_with(tenant_scope=scope),
            "app.models": module_with(
                AgentScenario=SimpleNamespace(objects=SimpleNamespace(get_default_scenario=self.get_default_scenario)),
                AIAnalytics=SimpleNamespace(
                    objects=SimpleNamespace(filter=lambda **kwargs: self.analytics_query()),
                    created_at=SimpleNamespace(desc=lambda: None),
                ),
                BotAction=SimpleNamespace(objects=SimpleNamespace()),
                CollectedItem=SimpleNamespace(objects=SimpleNamespace(for_source=self.for_source)),
                Source=SimpleNamespace(
                    objects=SimpleNamespace(
                        filter=lambda **kwargs: SimpleNamespace(select_related=AsyncMock(return_value=[self.source]))
                    )
                ),
            ),
            "app.services.ai.trigger_evaluator": module_with(
                trigger_evaluator=SimpleNamespace(should_analyze=self.should_analyze)
            ),
            "app.services.social.guards": module_with(
                extract_target_user=lambda payload: None, guards_checker=SimpleNamespace()
            ),
            "app.types": module_with(BotActionStatus=SimpleNamespace(PENDING="pending")),
            "app.services.ai.analyzer": module_with(
                AIAnalyzer=lambda: SimpleNamespace(analyze_content=self.analyze_content)
            ),
            "app.services.ai.dedup": module_with(analysed_hashes=lambda analytics: []),
            "app.core.database": module_with(new_session=AsyncMock()),
            "app.models.managers.agent_task_manager": module_with(
                AgentTaskManager=SimpleNamespace(parse_date=lambda v: v)
            ),
        }
        patcher = patch.dict("sys.modules", modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.handlers = load_isolated_source("_analyze_log_privacy_test", "app/jobs/handlers.py", imports=modules)
        self.logger = self.handlers.logger

    async def run_analyze(self, payload=None):
        return await self.handlers.handle_analyze(payload or {})

    def assert_private(self, logs, event, level, source_id, code):
        self.assertEqual(len(logs.records), 1)
        record = logs.records[0]
        self.assertEqual(record.levelno, level)
        self.assertEqual(record.msg, event)
        self.assertEqual(record.args, (source_id, code))
        self.assertNotIn(SECRET, record.getMessage())
        self.assertNotIn(SECRET, repr(record.args))
        self.assertIsNone(record.exc_info)
        self.assertIsNone(record.exc_text)
        self.assertIsNone(record.stack_info)

    async def test_staged_processing_failure_is_private_and_preserves_accounting(self):
        self.for_source.side_effect = RuntimeError(SECRET)
        with self.assertLogs(self.logger, level="WARNING") as logs:
            stats = await self.run_analyze()
        self.assert_private(
            logs, "analyze_staged_processing_failed source_id=%s error_code=%s", logging.WARNING, 23, "runtime_error"
        )
        self.assertEqual(stats["error"], 1)
        self.assertEqual(stats["staged_errors"], 1)
        self.assertEqual(stats["error_sources"], [SECRET])
        self.assertEqual(stats["per_source"][0]["staged_error"], True)

    async def test_outer_source_failure_is_private_and_preserves_accounting(self):
        self.analytics_query = lambda: RaisingQueryDouble(TimeoutError(SECRET))
        with self.assertLogs(self.logger, level="ERROR") as logs:
            stats = await self.run_analyze()
        self.assert_private(logs, "analyze_source_failed source_id=%s error_code=%s", logging.ERROR, 23, "timeout")
        self.assertEqual(stats["error"], 1)
        self.assertEqual(stats["staged_errors"], 0)
        self.assertEqual(stats["error_sources"], [SECRET])

    async def test_malformed_source_ids_are_suppressed_not_stringified(self):
        class DangerousId:
            def __str__(self):
                raise AssertionError("Logging must not stringify unexpected IDs")

            def __repr__(self):
                raise AssertionError("Logging must not repr unexpected IDs")

        cases = [
            ("secret_string", SECRET),
            ("bool_true", True),
            ("none", None),
            ("zero", 0),
            ("negative", -1),
            ("huge", 2**63),
            ("very_huge", 2**100),
            ("dangerous_object", DangerousId()),
        ]
        for label, bad_id in cases:
            with self.subTest(case=label):
                self.for_source.side_effect = RuntimeError(SECRET)
                self.source.id = bad_id
                with self.assertLogs(self.logger, level="WARNING") as logs:
                    await self.run_analyze()
                self.assert_private(
                    logs,
                    "analyze_staged_processing_failed source_id=%s error_code=%s",
                    logging.WARNING,
                    None,
                    "runtime_error",
                )
        self.source.id = 23

    async def test_valid_source_id_bounds_remain_correlatable(self):
        for good_id in (1, 2**63 - 1):
            with self.subTest(source_id=good_id):
                self.for_source.side_effect = RuntimeError(SECRET)
                self.source.id = good_id
                with self.assertLogs(self.logger, level="WARNING") as logs:
                    await self.run_analyze()
                self.assert_private(
                    logs,
                    "analyze_staged_processing_failed source_id=%s error_code=%s",
                    logging.WARNING,
                    good_id,
                    "runtime_error",
                )
        self.source.id = 23

    async def test_exception_class_name_is_never_formatted(self):
        class DangerousError(Exception):
            def __str__(self):
                raise AssertionError("Logging must not stringify exceptions")

        DangerousError.__name__ = SECRET
        self.for_source.side_effect = DangerousError()
        with self.assertLogs(self.logger, level="WARNING") as logs:
            await self.run_analyze()
        self.assert_private(
            logs, "analyze_staged_processing_failed source_id=%s error_code=%s", logging.WARNING, 23, "unexpected_error"
        )

    async def test_cancellation_propagates_without_logging(self):
        self.for_source.side_effect = KeyboardInterrupt()
        with self.assertNoLogs(self.logger, level="WARNING"):
            with self.assertRaises(KeyboardInterrupt):
                await self.run_analyze()

    async def test_clean_run_logs_nothing_and_preserves_skip_accounting(self):
        with self.assertNoLogs(self.logger, level="WARNING"):
            stats = await self.run_analyze()
        self.assertEqual(stats["error"], 0)
        self.assertEqual(stats["staged_errors"], 0)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(stats["sources"], 1)


if __name__ == "__main__":
    unittest.main()
