"""Run directly; actual collection/handler source, no app/DB/network bootstrap.

python tests/test_monitored_collection_errors.py
Local query/collection doubles do not prove PostgreSQL or provider behavior.
"""

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = "PRIVATE-PROVIDER-ERROR"


class AuthorizationRequired(Exception):
    def __init__(self, message=PRIVATE):
        self.hint = PRIVATE
        super().__init__(message)


def load(name, path, imports):
    return load_isolated_source(name, ROOT / path, imports)


class MonitoredCollectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.parent = SimpleNamespace(id=1, name="group", external_id="group", platform_id=3, params={})
        self.users = [SimpleNamespace(id=i, name=f"user{i}") for i in (7, 8, 9)]
        self.resolve = AsyncMock(side_effect=self.users)
        self.filter = Mock(side_effect=lambda **kwargs: SimpleNamespace(first=self.resolve))
        self.source = SimpleNamespace(objects=SimpleNamespace(filter=self.filter))
        self.notify = SimpleNamespace(create=AsyncMock())
        self.modules = {
            "app.core.tenant_context": SimpleNamespace(current_tenant_id=Mock(), is_bypass=Mock(), tenant_scope=Mock()),
            "app.models": SimpleNamespace(Platform=Mock(), Source=self.source),
            "app.services.ai.analyzer": SimpleNamespace(AIAnalyzer=Mock()),
            "app.services.social.factory": SimpleNamespace(get_social_client=Mock()),
            "app.services.social.credentials": SimpleNamespace(AuthorizationRequired=AuthorizationRequired),
            "app.types": SimpleNamespace(
                SourceType=SimpleNamespace(USER=SimpleNamespace(name="USER")), NotificationType=Mock()
            ),
            "app.services.notifications.messenger": SimpleNamespace(messenger_service=Mock()),
            "app.services.notifications.service": SimpleNamespace(notify=self.notify),
        }
        self.module = load("_monitored_collector", "app/services/monitoring/collector.py", self.modules)
        self.collector = self.module.ContentCollector()
        self.collector.collect_from_source = AsyncMock()
        self.handlers = load(
            "_monitored_collect_handler",
            "app/jobs/handlers.py",
            {
                "app.services.social.credentials": self.modules["app.services.social.credentials"],
                "app.services.monitoring.collector": SimpleNamespace(ContentCollector=lambda: self.collector),
            },
        )
        self.handlers._load_task = AsyncMock(return_value=SimpleNamespace())
        self.payload = {"monitored_users": ["a", "b", "c"], "analyze_inline": False, "force_reanalyze": True}
        self.handlers._task_payload = Mock(side_effect=lambda task: self.payload)
        self.handlers._resolve_sources = AsyncMock(return_value=[self.parent])
        self.handlers._resolve_content_window = Mock(return_value={})

    async def collect(self, **kwargs):
        return await self.collector.collect_monitored_users(
            self.parent, monitored_users=["a", "b", "c"], **kwargs
        )

    async def handle(self, result):
        self.collector.collect_monitored_users = AsyncMock(return_value=result)
        return await self.handlers.handle_collect({"job_id": 42})

    def assert_private(self, value):
        self.assertNotIn(PRIVATE, repr(value))

    async def test_partial_failure_preserves_successes_and_continues(self):
        self.collector.collect_from_source.side_effect = [
            {"content_count": 4, "new_items": 2}, RuntimeError(PRIVATE), {"content_count": 3, "new_items": 1}
        ]
        result = await self.collect(analyze=False, force_reanalyze=True, run_id=42)
        self.assertEqual((result["successful"], result["failed"], result["empty"]), (2, 1, 0))
        self.assertEqual((result["total_items"], result["total_new_items"]), (7, 3))
        self.assertEqual(self.collector.collect_from_source.await_count, 3)
        self.collector.collect_from_source.assert_any_await(
            self.users[2], analyze=False, force_reanalyze=True, run_id=42
        )
        self.assert_private(result)

    async def test_missing_user_is_a_failure_but_remaining_users_are_collected(self):
        self.resolve.side_effect = [None, self.users[1], self.users[2]]
        self.collector.collect_from_source.side_effect = [{"content_count": 2, "new_items": 1}, None]
        result = await self.collect()
        self.assertEqual((result["total_users"], result["successful"], result["failed"], result["empty"]), (3, 1, 1, 1))
        self.assertEqual(self.collector.collect_from_source.await_count, 2)

    async def test_lookup_failure_preserves_earlier_totals(self):
        self.resolve.side_effect = [self.users[0], RuntimeError(PRIVATE), self.users[2]]
        self.collector.collect_from_source.side_effect = [
            {"content_count": 2, "new_items": 1}, {"content_count": 3, "new_items": 0}
        ]
        result = await self.collect()
        self.assertEqual((result["total_items"], result["failed"]), (5, 1))
        self.assertEqual(self.collector.collect_from_source.await_count, 2)
        self.assert_private(result)

    async def test_none_and_zero_item_response_are_normal_empty(self):
        self.collector.collect_from_source.side_effect = [None, {"content_count": 0, "new_items": 0}, None]
        result = await self.collect()
        self.assertEqual((result["empty"], result["failed"], result["successful"]), (3, 0, 0))

    async def test_auth_failure_is_subset_not_additional_failure(self):
        self.collector.collect_from_source.side_effect = [
            AuthorizationRequired(), RuntimeError(PRIVATE), {"content_count": 2, "new_items": 1}
        ]
        result = await self.collect()
        self.assertEqual((result["failed"], result["auth_required"], result["successful"]), (2, 1, 1))
        self.assert_private(result)

    async def test_default_usernames_and_platform_scope_are_preserved(self):
        self.parent.params = {"monitored_users": ["@a"]}
        self.collector.collect_from_source.return_value = {"content_count": 2, "new_items": 1}
        await self.collector.collect_monitored_users(self.parent, analyze=False, run_id=42)
        self.filter.assert_called_once_with(platform_id=3, external_id="a", source_type="USER")
        self.collector.collect_from_source.assert_awaited_once_with(
            self.users[0], analyze=False, force_reanalyze=False, run_id=42
        )
        self.modules["app.core.tenant_context"].tenant_scope.assert_not_called()
        self.notify.create.assert_not_awaited()
        self.modules["app.services.social.factory"].get_social_client.assert_not_called()

    async def test_explicit_empty_override_does_not_use_source_defaults(self):
        self.parent.params = {"monitored_users": ["a"]}
        result = await self.collector.collect_monitored_users(self.parent, monitored_users=[])
        self.assertTrue(all(value == 0 for value in result.values()))
        self.filter.assert_not_called()
        self.collector.collect_from_source.assert_not_awaited()

    async def test_legacy_new_item_fallback_remains_compatible(self):
        self.collector.collect_from_source.return_value = {"content_count": 2}
        result = await self.collect()
        self.assertEqual((result["total_items"], result["total_new_items"]), (6, 6))

    async def test_malformed_child_counters_do_not_partially_update_totals(self):
        self.collector.collect_from_source.side_effect = [
            {"content_count": 2, "new_items": 1}, {"content_count": 10, "new_items": PRIVATE},
            {"content_count": 3, "new_items": 1},
        ]
        result = await self.collect()
        self.assertEqual(
            (result["successful"], result["failed"], result["total_items"], result["total_new_items"]), (2, 1, 5, 2)
        )
        self.assert_private(result)

    async def test_malformed_result_and_boolean_counts_are_not_empty_or_coerced(self):
        for result in ({}, [], False, {"content_count": True}, {"content_count": -1}, {"content_count": "2"}):
            self.resolve.side_effect = self.users
            self.collector.collect_from_source.side_effect = None
            self.collector.collect_from_source.return_value = result
            actual = await self.collect()
            self.assertEqual((actual["failed"], actual["empty"], actual["total_items"]), (3, 0, 0))

    async def test_cancelled_collection_propagates_without_collecting_remaining_user(self):
        self.collector.collect_from_source.side_effect = [None, asyncio.CancelledError(), None]
        with self.assertRaises(asyncio.CancelledError):
            await self.collect()
        self.assertEqual(self.collector.collect_from_source.await_count, 2)

    async def test_cancelled_lookup_propagates(self):
        self.resolve.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.collect()
        self.collector.collect_from_source.assert_not_awaited()

    async def test_new_failure_log_never_emits_exception_or_traceback(self):
        self.collector.collect_from_source.side_effect = RuntimeError(PRIVATE)
        with self.assertLogs(self.module.logger, level="WARNING") as logs:
            await self.collect()
        self.assert_private(logs.output)
        self.assertTrue(all(record.exc_info is None for record in logs.records))

    async def test_new_failure_log_does_not_stringify_untrusted_parent_id(self):
        self.parent.id = PRIVATE
        self.collector.collect_from_source.side_effect = RuntimeError(PRIVATE)
        with self.assertLogs(self.module.logger, level="WARNING") as logs:
            await self.collect()
        self.assert_private(logs.output)
        self.assertTrue(all(record.args == (None,) for record in logs.records))

    async def test_handler_partial_success_is_also_one_affected_parent_error(self):
        result = await self.handle({"total_items": 7, "total_new_items": 3, "failed": 2, "auth_required": 1})
        self.assertEqual((result["sources"], result["collected"], result["items"], result["new_items"]), (1, 1, 7, 3))
        self.assertEqual((result["error"], result["empty"]), (1, 0))
        self.assertEqual(result["error_sources"], ["group"])
        per = result["per_source"][0]
        self.assertEqual(
            (per["outcome"], per["error"], per["monitored_errors"], per["auth_required"]), ("collected", True, 2, True)
        )
        self.assertEqual(result["error_messages"], [])

    async def test_handler_all_failed_is_not_empty(self):
        result = await self.handle({"total_items": 0, "total_new_items": 0, "failed": 3})
        self.assertEqual((result["error"], result["empty"], result["collected"]), (1, 0, 0))
        self.assertEqual(result["per_source"][0]["outcome"], "error")

    async def test_handler_all_auth_failures_keep_authorization_outcome_and_safe_hint(self):
        result = await self.handle(
            {"total_items": 0, "total_new_items": 0, "failed": 2, "auth_required": 2, "hint": PRIVATE}
        )
        per = result["per_source"][0]
        self.assertEqual(per["outcome"], "auth_required")
        self.assertTrue(per["auth_hint"])
        self.assert_private(result)

    async def test_handler_normal_empty_is_not_failure(self):
        result = await self.handle({"total_items": 0, "total_new_items": 0, "failed": 0, "empty": 3})
        self.assertEqual((result["empty"], result["error"]), (1, 0))
        self.assertNotIn("error", result["per_source"][0])

    async def test_handler_legacy_or_malformed_failure_counters_are_not_coerced(self):
        for failed in (None, False, True, "2", PRIVATE, -1, [], {}):
            result = await self.handle(
                {"total_items": 4, "total_new_items": 1, "failed": failed, "auth_required": failed}
            )
            self.assertEqual((result["collected"], result["error"]), (1, 0))
            self.assertNotIn("monitored_errors", result["per_source"][0])
            self.assert_private(result)

    async def test_handler_auth_counter_alone_is_observed_not_added(self):
        result = await self.handle({"total_items": 1, "total_new_items": 0, "auth_required": 2})
        self.assertEqual(result["error"], 1)
        self.assertEqual(result["per_source"][0]["monitored_errors"], 2)

    async def test_handler_continues_between_parent_sources_and_keeps_overrides(self):
        second = SimpleNamespace(id=2, name="second", external_id="second", platform_id=3, params={})
        self.handlers._resolve_sources.return_value = [self.parent, second]
        self.collector.collect_monitored_users = AsyncMock(side_effect=[
            {"total_items": 2, "total_new_items": 1, "failed": 2},
            {"total_items": 3, "total_new_items": 1, "failed": 0},
        ])
        result = await self.handlers.handle_collect({"job_id": 42})
        self.assertEqual((result["sources"], result["collected"], result["items"], result["error"]), (2, 2, 5, 1))
        self.collector.collect_monitored_users.assert_any_await(
            self.parent, analyze=False, monitored_users=["a", "b", "c"], force_reanalyze=True, run_id=42
        )

    async def test_handler_excluded_parent_is_not_collected_or_failed(self):
        self.payload["excluded_users"] = ["group"]
        self.collector.collect_monitored_users = AsyncMock()
        result = await self.handlers.handle_collect({})
        self.assertEqual((result["excluded"], result["sources"], result["error"]), (1, 0, 0))
        self.collector.collect_monitored_users.assert_not_awaited()

    async def test_handler_cancellation_is_not_flattened_into_stats(self):
        self.collector.collect_monitored_users = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await self.handlers.handle_collect({})

    async def test_real_collector_to_handler_keeps_partial_totals_and_digest_error_marker(self):
        self.collector.collect_from_source.side_effect = [
            {"content_count": 2, "new_items": 1}, RuntimeError(PRIVATE), {"content_count": 3, "new_items": 1}
        ]
        result = await self.handlers.handle_collect({"job_id": 42})
        self.assertEqual((result["items"], result["new_items"], result["error"]), (5, 2, 1))
        self.assertEqual(result["per_source"][0]["monitored_errors"], 1)
        coverage = load("_monitored_coverage", "app/services/digest/coverage.py", {})
        self.assertTrue(coverage._reported_errors(result, {self.parent.id}))
        self.assert_private(result)

    async def test_direct_source_branch_remains_unchanged(self):
        self.payload["monitored_users"] = []
        self.collector.collect_from_source.return_value = {"content_count": 3, "new_items": 1}
        result = await self.handlers.handle_collect({"job_id": 42})
        self.assertEqual((result["items"], result["new_items"], result["error"]), (3, 1, 0))
        self.assertNotIn("monitored_errors", result["per_source"][0])


if __name__ == "__main__":
    unittest.main()
