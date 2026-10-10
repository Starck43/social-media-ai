"""Prepared standalone actual-route tests with module-local infrastructure doubles.

Owner: python tests/test_web_job_run_outcome.py
No application/DB/provider/live imports or route-server startup. These cases
verify route wiring/outcome flashes, not actual auth rules or PostgreSQL claims.
"""

import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]


class Router:
    def __init__(self, **kwargs):
        self.prefix = kwargs["prefix"]
        self.routes = []

    def get(self, path, **kwargs):
        return self.route("get", path)

    def post(self, path, **kwargs):
        return self.route("post", path)

    def route(self, method, path):
        def register(function):
            self.routes.append((method, path, function.__name__))
            return function

        return register


class Redirect:
    def __init__(self, url, status_code):
        self.url = url
        self.status_code = status_code


class JobRunOutcomeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.flashes = []
        self.scopes = []
        self.events = []
        self.csrf = True
        self.superuser_denial = None
        self.permission_denial = None
        self.job = SimpleNamespace(id=7, tenant_id=31, status="failed")
        self.get = AsyncMock(return_value=self.job)
        self.run = AsyncMock(return_value={"status": "done", "result": {"items": 2}})
        self.request = SimpleNamespace(state=SimpleNamespace(tenant_id=31))

        @contextmanager
        def tenant_scope(tenant_id=None, **kwargs):
            self.scopes.append(tenant_id)
            yield

        def csrf(request, token):
            self.events.append("csrf")
            return self.csrf

        def superuser(request, **kwargs):
            self.events.append("superuser")
            return self.superuser_denial

        def permission(request, model, action, **kwargs):
            self.events.append((model, action))
            return self.permission_denial

        deps = SimpleNamespace(
            action_tenant_id=lambda request, tenant_id: 31,
            add_flash=lambda request, kind, text: self.flashes.append((kind, text)),
            ensure_csrf=csrf,
            guard_superuser=superuser,
            guard_web=permission,
            plural=lambda *args: "",
            render=AsyncMock(),
            tenant_filter_context=AsyncMock(),
        )
        self.source = load_isolated_source(
            "app.web._job_outcome_isolated",
            ROOT / "app/web/jobs.py",
            {
                "fastapi": SimpleNamespace(
                    APIRouter=Router, Form=lambda default=None, **kwargs: default, Request=object
                ),
                "fastapi.responses": SimpleNamespace(RedirectResponse=Redirect),
                "app.models.job": SimpleNamespace(Job=SimpleNamespace(objects=SimpleNamespace(get=self.get))),
                "app.web.deps": deps,
                "app.core.tenant_context": SimpleNamespace(tenant_scope=tenant_scope),
                "app.jobs.dispatcher": SimpleNamespace(run_job_now=self.run),
            },
        )

    async def attempt(self):
        response = await self.source.job_run(self.request, 7, token="csrf", tenant_id=31)
        self.assertEqual((response.url, response.status_code), ("/app/jobs", 302))
        return response

    def assert_not_success(self):
        self.assertFalse(any(kind == "success" for kind, text in self.flashes))

    async def test_missing_claim_is_not_reported_as_success(self):
        self.run.return_value = None
        await self.attempt()
        self.assert_not_success()
        self.assertIn("Запуск не начат", self.flashes[-1][1])
        self.run.assert_awaited_once_with(7, allow_retry=False)

    async def test_only_done_outcome_reports_completed(self):
        await self.attempt()
        self.assertEqual(self.flashes[-1], ("success", "Задание выполнено"))
        self.assertEqual(self.events, ["csrf", "superuser", ("agenttask", "update")])
        self.get.assert_awaited_once_with(id=7, tenant_id=31)
        self.assertEqual(self.scopes, [31])
        self.run.assert_awaited_once_with(7, allow_retry=False)

    async def test_failed_outcome_preserves_failure_flash(self):
        self.run.return_value = {"status": "failed", "error": "safe_static_error"}
        await self.attempt()
        self.assertEqual(self.flashes[-1], ("error", "Задание снова упало: safe_static_error"))
        self.assert_not_success()

    async def test_nonterminal_unknown_or_missing_status_never_reports_success(self):
        for status in ("pending", "running", "unknown", "ok", None, {}, []):
            with self.subTest(status=status):
                self.flashes.clear()
                self.run.return_value = {"status": status}
                await self.attempt()
                self.assert_not_success()
                self.assertEqual(self.flashes[-1][0], "error")
        self.run.return_value = {}
        await self.attempt()
        self.assert_not_success()

    async def test_malformed_dispatch_result_never_reports_success(self):
        for result in ([], "done", 1, True):
            with self.subTest(result=result):
                self.flashes.clear()
                self.run.return_value = result
                await self.attempt()
                self.assert_not_success()
                self.assertEqual(self.flashes[-1][0], "error")

    async def test_skipped_done_outcome_is_distinct_from_executed(self):
        self.run.return_value = {"status": "done", "result": {"status": "skipped", "reason": "cost_cap"}}
        await self.attempt()
        self.assertEqual(self.flashes[-1][0], "info")
        self.assertIn("пропущено", self.flashes[-1][1])
        self.assert_not_success()

    async def test_bad_csrf_stops_before_any_guard_lookup_or_execution(self):
        self.csrf = False
        await self.attempt()
        self.assertEqual(self.events, ["csrf"])
        self.get.assert_not_awaited()
        self.run.assert_not_awaited()
        self.assert_not_success()

    async def test_superuser_denial_stops_before_permission_lookup_or_execution(self):
        denied = self.superuser_denial = Redirect("/app/", 302)
        response = await self.source.job_run(self.request, 7, token="csrf")
        self.assertIs(response, denied)
        self.assertEqual(self.events, ["csrf", "superuser"])
        self.get.assert_not_awaited()
        self.run.assert_not_awaited()

    async def test_update_denial_stops_before_job_lookup_or_execution(self):
        denied = self.permission_denial = Redirect("/app/jobs", 302)
        response = await self.source.job_run(self.request, 7, token="csrf")
        self.assertIs(response, denied)
        self.get.assert_not_awaited()
        self.run.assert_not_awaited()

    async def test_missing_job_does_not_call_dispatcher(self):
        self.get.return_value = None
        await self.attempt()
        self.run.assert_not_awaited()
        self.assert_not_success()

    async def test_nonretryable_row_does_not_call_dispatcher(self):
        for status in ("running", "pending", "done"):
            with self.subTest(status=status):
                self.job.status = status
                await self.attempt()
                self.run.assert_not_awaited()
                self.assert_not_success()

    async def test_dispatcher_error_propagates_without_success_flash(self):
        self.run.side_effect = RuntimeError("safe outcome error")
        with self.assertRaisesRegex(RuntimeError, "safe outcome error"):
            await self.attempt()
        self.assertEqual(self.flashes, [])

    def test_endpoint_and_retry_policy_remain_unchanged(self):
        self.assertEqual(self.source.RETRYABLE, {"failed"})
        self.assertEqual(self.source.router.prefix, "/jobs")
        self.assertIn(("post", "/{job_id}/run", "job_run"), self.source.router.routes)


if __name__ == "__main__":
    unittest.main()
