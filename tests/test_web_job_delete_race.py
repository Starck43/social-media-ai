"""Prepared standalone regression tests for the job_delete read/delete race.

Owner: python tests/test_web_job_delete_race.py
No application/DB/provider/live imports or route-server startup. Module-local
import doubles only: these cases verify the handler's conditional delete and
outcome flashes, not actual auth rules or PostgreSQL claims.
"""

import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]

DELETABLE = frozenset({"pending", "done", "failed"})


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


class DeleteFailure(RuntimeError):
    """Stand-in for a database failure raised by the conditional delete."""


class JobDeleteRaceTests(unittest.IsolatedAsyncioTestCase):
    def build(self, *, action_tenant=31):
        self.flashes = []
        self.events = []
        self.scopes = []
        self.csrf = True
        self.superuser_denial = None
        self.permission_denial = None
        self.job = SimpleNamespace(id=7, tenant_id=31, status="done")
        self.get = AsyncMock(return_value=self.job)
        self.delete = AsyncMock(return_value=1)
        self.request = SimpleNamespace(state=SimpleNamespace(tenant_id=31))

        @contextmanager
        def tenant_scope(tenant_id=None, **kwargs):
            self.scopes.append((tenant_id, kwargs))
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
            action_tenant_id=lambda request, tenant_id: action_tenant,
            add_flash=lambda request, kind, text: self.flashes.append((kind, text)),
            ensure_csrf=csrf,
            guard_superuser=superuser,
            guard_web=permission,
            plural=lambda *args: "",
            render=AsyncMock(),
            tenant_filter_context=AsyncMock(),
        )
        self.source = load_isolated_source(
            "app.web._job_delete_race_isolated",
            ROOT / "app/web/jobs.py",
            {
                "fastapi": SimpleNamespace(
                    APIRouter=Router, Form=lambda default=None, **kwargs: default, Request=object
                ),
                "fastapi.responses": SimpleNamespace(RedirectResponse=Redirect),
                "app.models.job": SimpleNamespace(
                    Job=SimpleNamespace(objects=SimpleNamespace(get=self.get, delete=self.delete))
                ),
                "app.web.deps": deps,
                "app.core.tenant_context": SimpleNamespace(tenant_scope=tenant_scope),
            },
        )

    def setUp(self):
        self.build()

    async def attempt_delete(self):
        response = await self.source.job_delete(self.request, 7, token="csrf", tenant_id=31)
        self.assertEqual((response.url, response.status_code), ("/app/jobs", 302))
        return response

    def assert_success(self):
        self.assertEqual(self.flashes[-1], ("success", "Задание #7 удалено"))

    def assert_error(self):
        self.assertEqual(self.flashes[-1][0], "error")
        self.assertNotEqual(self.flashes[-1][1], "Задание #7 удалено")

    async def test_deletable_statuses_succeed_on_exact_single_delete(self):
        for status in sorted(DELETABLE):
            with self.subTest(status=status):
                self.build()
                self.job.status = status
                await self.attempt_delete()
                self.assert_success()
                self.get.assert_awaited_once_with(id=7, tenant_id=31)
                self.delete.assert_awaited_once_with(id=7, tenant_id=31, status__in=DELETABLE)
                self.assertEqual(self.events[:3], ["csrf", "superuser", ("agenttask", "delete")])

    async def test_row_reclaimed_to_running_after_lookup_is_not_deleted(self):
        self.delete.return_value = 0  # The row left the deletable set between read and delete.
        await self.attempt_delete()
        self.assert_error()
        self.assertIn("состояние изменилось", self.flashes[-1][1])
        self.delete.assert_awaited_once_with(id=7, tenant_id=31, status__in=DELETABLE)

    async def test_row_disappeared_before_delete_is_not_reported_as_success(self):
        self.delete.return_value = 0
        await self.attempt_delete()
        self.assert_error()
        self.assertIn("не удалено", self.flashes[-1][1])
        self.delete.assert_awaited_once()

    async def test_missing_row_reports_not_found_and_never_deletes(self):
        self.get.return_value = None
        await self.attempt_delete()
        self.assertEqual(self.flashes[-1], ("error", "Задание не найдено"))
        self.delete.assert_not_awaited()

    async def test_running_status_refuses_before_any_delete(self):
        self.job.status = "running"
        await self.attempt_delete()
        self.assert_error()
        self.delete.assert_not_awaited()
        self.get.assert_awaited_once_with(id=7, tenant_id=31)

    async def test_tenant_filter_travels_with_the_conditional_delete(self):
        await self.attempt_delete()
        self.assert_success()
        self.assertEqual(self.delete.await_args.kwargs["tenant_id"], 31)
        self.assertIn(31, [scope[0] for scope in self.scopes])

    async def test_superuser_bypass_keeps_id_and_status_condition_without_tenant(self):
        self.build(action_tenant=None)
        await self.attempt_delete()
        self.assert_success()
        self.delete.assert_awaited_once_with(id=7, status__in=DELETABLE)
        self.assertEqual(self.scopes, [(None, {"bypass": True})])

    async def test_guards_precede_lookup_and_delete(self):
        cases = (
            ("csrf", False, ["csrf"]),
            ("superuser_denial", Redirect("/app/", 302), ["csrf", "superuser"]),
            ("permission_denial", Redirect("/app/jobs", 302), ["csrf", "superuser", ("agenttask", "delete")]),
        )
        for attribute, denial, expected_events in cases:
            with self.subTest(guard=attribute):
                self.build()
                setattr(self, attribute, denial)
                response = await self.source.job_delete(self.request, 7, token="csrf", tenant_id=31)
                self.assertEqual(self.events, expected_events)
                if attribute != "csrf":
                    self.assertIs(response, denial)
                else:
                    self.assertEqual((response.url, response.status_code), ("/app/jobs", 302))
                self.get.assert_not_awaited()
                self.delete.assert_not_awaited()

    async def test_delete_exception_propagates_without_flash_or_second_delete(self):
        self.delete.side_effect = DeleteFailure("database gone")
        with self.assertRaises(DeleteFailure):
            await self.attempt_delete()
        self.assertEqual(self.flashes, [])
        self.delete.assert_awaited_once()

    async def test_more_than_one_deleted_row_is_not_reported_as_success(self):
        self.delete.return_value = 2
        await self.attempt_delete()
        self.assert_error()
        self.delete.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
