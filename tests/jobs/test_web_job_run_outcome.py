"""No-DB isolated route checks for asynchronous Job replay."""

import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]


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


class JobReplayRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.flashes, self.scopes, self.events = [], [], []
        self.csrf = True
        self.superuser_denial = None
        self.permission_denial = None
        self.job = SimpleNamespace(id=7, tenant_id=31, status="failed", job_type="collect", payload={}, result={}, error=None)
        self.get = AsyncMock(return_value=self.job)
        self.enqueue = AsyncMock(return_value=SimpleNamespace(id=8))
        self.launch = Mock()
        self.request = SimpleNamespace(
            state=SimpleNamespace(tenant_id=31, web_user=SimpleNamespace(id=9)), query_params={}
        )

        @contextmanager
        def tenant_scope(tenant_id=None, **kwargs):
            self.scopes.append((tenant_id, kwargs))
            yield

        self.scope = tenant_scope

        def guard(request, model, action, **kwargs):
            self.events.append((model, action))
            return None

        deps = SimpleNamespace(
            action_tenant_id=lambda request, tenant_id: 31,
            add_flash=lambda request, kind, text: self.flashes.append((kind, text)),
            ensure_csrf=lambda request, token: self.csrf,
            guard_superuser=lambda *args, **kwargs: None,
            guard_web=guard,
            plural=lambda *args: "",
            render=AsyncMock(),
            tenant_filter_context=AsyncMock(),
        )
        self.source = load_isolated_source(
            "app.web._job_replay_isolated",
            ROOT / "app/web/jobs.py",
            {
                "fastapi": SimpleNamespace(APIRouter=Router, Form=lambda default=None, **kwargs: default, Request=object),
                "fastapi.responses": SimpleNamespace(RedirectResponse=Redirect),
                **dict.fromkeys(('app.models.scheduling.job',), SimpleNamespace(Job=SimpleNamespace(objects=SimpleNamespace(get=self.get, enqueue_replay=self.enqueue)))),
                "app.web.deps": deps,
                "app.core.tenant_context": SimpleNamespace(tenant_scope=tenant_scope),
                "app.jobs.replay_policy": SimpleNamespace(
                    can_replay=lambda job: job.status == "failed",
                    replay_block_reason=lambda job: None if job.status == "failed" else "Повтор недоступен",
                ),
                "app.jobs.dispatcher": SimpleNamespace(launch_job_background=self.launch),
                "app.models.managers.job_manager": SimpleNamespace(JobManager=object),
            },
        )

    async def test_replay_enqueues_new_job_and_launches_without_waiting(self):
        response = await self.source.job_run(self.request, 7, token="csrf", tenant_id=31)
        self.assertEqual((response.url, response.status_code), ("/app/jobs", 302))
        self.enqueue.assert_awaited_once_with(7, initiated_by_user_id=9)
        self.launch.assert_called_once_with(8, allow_retry=False)
        self.assertIn("исходный job #7 сохранён", self.flashes[-1][1])

    async def test_bad_csrf_stops_before_database_or_launch(self):
        self.csrf = False
        await self.source.job_run(self.request, 7, token="bad", tenant_id=31)
        self.get.assert_not_awaited()
        self.enqueue.assert_not_awaited()
        self.launch.assert_not_called()

    async def test_unsafe_row_is_not_enqueued(self):
        self.job.status = "running"
        await self.source.job_run(self.request, 7, token="csrf", tenant_id=31)
        self.enqueue.assert_not_awaited()
        self.launch.assert_not_called()

    def test_endpoint_and_legacy_retry_constant(self):
        self.assertEqual(self.source.RETRYABLE, {"failed"})
        self.assertIn(("post", "/{job_id}/run", "job_run"), self.source.router.routes)


# Shared cancellation-fencing route tests reuse the fixture initializer.
JobRunOutcomeTests = JobReplayRouteTests
