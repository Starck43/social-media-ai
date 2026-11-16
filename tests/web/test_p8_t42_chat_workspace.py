"""Isolated permission and tenant contracts for the chat workspace panel."""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.source_import_isolation import load_isolated_source

__source_only__ = True


class _Field:
    def desc(self):
        return self

    def ilike(self, expression, **kwargs):
        return (expression, kwargs)


class _Query:
    def __init__(self, rows, calls):
        self.rows = rows
        self.calls = calls
        self._offset = 0
        self._limit = None

    def filter(self, *args, **kwargs):
        self.calls.append(("filter", args, kwargs))
        return self

    def order_by(self, *args):
        self.calls.append(("order_by", args))
        return self

    def offset(self, value):
        self.calls.append(("offset", value))
        self._offset = value
        return self

    def limit(self, value):
        self.calls.append(("limit", value))
        self._limit = value
        return self

    def __await__(self):
        async def result():
            rows = self.rows[self._offset :]
            return rows if self._limit is None else rows[: self._limit]

        return result().__await__()


def _module(name, **values):
    module = ModuleType(name)
    module.__dict__.update(values)
    return module


def _request(*, user=SimpleNamespace(id=7), tenant_id=12, can_chat=True, task_view=True):
    return SimpleNamespace(
        state=SimpleNamespace(
            web_user=user,
            tenant_id=tenant_id,
            web_perms=SimpleNamespace(can_open_chat=can_chat),
        ),
        _task_view=task_view,
    )


def _load_workspace(monkeypatch, *, rows=(), config=None, result=None, calls=None):
    calls = calls if calls is not None else []
    active_tenant = {"id": None}

    @contextmanager
    def tenant_scope(tenant_id):
        previous = active_tenant["id"]
        active_tenant["id"] = tenant_id
        try:
            yield
        finally:
            active_tenant["id"] = previous

    async def task_configuration(task_id):
        calls.append(("configuration", task_id, active_tenant["id"]))
        return config

    async def task_result(task_id, *, limit):
        calls.append(("result", task_id, limit, active_tenant["id"]))
        return result

    task_query = _Query(list(rows), calls)

    def task_filter(**kwargs):
        calls.append(("manager_filter", kwargs))
        return task_query

    task_model = SimpleNamespace(
        objects=SimpleNamespace(filter=task_filter),
        id=_Field(),
        name=_Field(),
    )
    deps = _module("app.web.deps", perms_can=lambda request, model, action: request._task_view)
    tenant = _module("app.core.tenant_context", tenant_scope=tenant_scope)
    models = _module("app.models", AgentTask=task_model)
    results = _module(
        "app.services.task_results",
        task_configuration=task_configuration,
        task_result=task_result,
    )
    module = load_isolated_source(
        "app.web._p8_t42_chat_workspace_under_test",
        "app/web/chat_workspace.py",
        imports={
            "app.web.deps": deps,
            "app.core.tenant_context": tenant,
            "app.models": models,
            "app.services.task_results": results,
        },
    )
    return module, calls, active_tenant


@pytest.mark.parametrize(
    "web_request",
    [
        _request(user=None),
        _request(user=SimpleNamespace(id="7")),
        _request(tenant_id=None),
        _request(can_chat=False),
        _request(task_view=False),
    ],
    ids=["no-user", "invalid-user-id", "no-active-workspace", "chat-denied", "task-view-denied"],
)
async def test_task_picker_denies_before_query(web_request, monkeypatch):
    module, calls, _ = _load_workspace(monkeypatch)

    response = await module.workspace_tasks(web_request, search="", offset=0)

    assert response.status_code == 403
    assert calls == []


async def test_picker_is_bounded_and_filters_to_active_workspace(monkeypatch):
    rows = [SimpleNamespace(id=index, name=f"Task {index}") for index in range(1, 23)]
    module, calls, active_tenant = _load_workspace(monkeypatch, rows=rows)

    response = await module.workspace_tasks(_request(), search="", offset=20)

    assert response.status_code == 200
    body = json.loads(response.body)
    assert body == {
        "tasks": [{"id": 21, "name": "Task 21"}, {"id": 22, "name": "Task 22"}],
        "next_offset": None,
    }
    assert ("manager_filter", {"tenant_id": 12, "job_type__in": ["collect", "analyze"]}) in calls
    assert ("offset", 20) in calls and ("limit", module.PAGE_SIZE + 1) in calls
    assert active_tenant["id"] is None


async def test_picker_search_treats_wildcards_as_literal_text(monkeypatch):
    module, calls, _ = _load_workspace(monkeypatch)

    response = await module.workspace_tasks(_request(), search="100%_ready", offset=0)

    assert response.status_code == 200
    assert ("filter", ((r"%100\%\_ready%", {"escape": "\\"}),), {}) in calls


def test_fastapi_rejects_out_of_range_picker_offset_before_query(monkeypatch):
    module, calls, _ = _load_workspace(monkeypatch)
    app = FastAPI()
    app.include_router(module.router)

    @app.middleware("http")
    async def add_workspace_identity(request, call_next):
        request.state.web_user = SimpleNamespace(id=7)
        request.state.tenant_id = 12
        request.state.web_perms = SimpleNamespace(can_open_chat=True)
        request._task_view = True
        return await call_next(request)

    response = TestClient(app).get("/workspace/tasks?offset=10001")

    assert response.status_code == 422
    assert calls == []


async def test_selected_task_uses_canonical_readers_and_projects_safe_snapshot(monkeypatch):
    definition = {
        "id": 44,
        "name": "Research task",
        "job_type": "analyze",
        "plan_status": "available",
        "plan": {
            "goal": "Research market",
            "platforms": ["vk"],
            "source_scope": "selected",
            "source_ids": [3, 4],
            "window": {"type": "rolling", "days": 7},
            "trigger": {"type": "once"},
            "pipeline": {"stages": ["collect", "analyze"]},
            "collection": {"limit": 50, "private_metadata": "must not leak"},
            "vk_access": {"credential_id": 99},
            "sensitive_payload": "must not leak",
        },
        "provider_payload": "must not leak",
    }
    result = {
        "job_status": "done",
        "coverage": "partial_or_bounded",
        "progress": {"phase": "done", "total": 5, "completed": 4, "updated_at": "now", "raw": "hidden"},
        "analyses": [
            {"id": 9, "title": "Summary", "content": "private"},
            {"id": 10, "title": "Second", "content": "private"},
        ],
        "raw_error": "hidden",
    }
    module, calls, _ = _load_workspace(monkeypatch, config=definition, result=result)

    response = await module.workspace_task(_request(), task_id=44)

    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["task_url"] == "/app/tasks/44"
    assert body["plan"] == {
        "goal": "Research market",
        "platforms": ["vk"],
        "source_scope": "selected",
        "source_count": 2,
        "window": {"type": "rolling", "days": 7},
        "trigger": {"type": "once"},
        "stages": ["collect", "analyze"],
        "limit": 50,
    }
    assert body["latest"]["progress"] == {
        "phase": "done",
        "total": 5,
        "completed": 4,
        "updated_at": "now",
    }
    assert body["latest"]["analyses"] == [
        {"title": "Summary", "url": "/app/analytics/9"},
        {"title": "Second", "url": "/app/analytics/10"},
    ]
    assert all(
        value not in response.body.decode() for value in ("must not leak", "private", "raw_error", "credential_id")
    )
    assert ("configuration", 44, 12) in calls
    assert ("result", 44, 3, 12) in calls


async def test_selected_task_hides_analysis_links_without_source_and_analysis_rights(monkeypatch):
    definition = {"id": 44, "name": "Research task", "job_type": "analyze", "plan_status": "legacy"}
    result = {"job_status": "done", "analyses": [{"id": 9, "title": "Private"}]}
    module, _, _ = _load_workspace(monkeypatch, config=definition, result=result)
    request = _request()
    permitted = {"agenttask"}
    module.perms_can = lambda _request, model, _action: model in permitted

    response = await module.workspace_task(request, task_id=44)

    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["latest"]["status"] == "done"
    assert body["latest"]["analyses"] == []


async def test_missing_or_foreign_task_is_not_disclosed(monkeypatch):
    module, calls, _ = _load_workspace(monkeypatch, config=None)

    response = await module.workspace_task(_request(), task_id=999)

    assert response.status_code == 404
    assert json.loads(response.body) == {"error": "Задача недоступна"}
    assert calls == [("configuration", 999, 12)]


async def test_non_social_task_is_not_selected_or_read_for_results(monkeypatch):
    module, calls, _ = _load_workspace(
        monkeypatch,
        config={"id": 999, "name": "Retention", "job_type": "prune", "plan_status": "legacy"},
    )

    response = await module.workspace_task(_request(), task_id=999)

    assert response.status_code == 404
    assert json.loads(response.body) == {"error": "Задача недоступна"}
    assert calls == [("configuration", 999, 12)]


async def test_invalid_selected_id_does_not_query_task_configuration(monkeypatch):
    module, calls, _ = _load_workspace(monkeypatch)

    response = await module.workspace_task(_request(), task_id=0)

    assert response.status_code == 404
    assert calls == []


async def test_plan_unavailable_state_does_not_forward_restricted_plan(monkeypatch):
    definition = {
        "id": 44,
        "name": "Research task",
        "job_type": "analyze",
        "plan_status": "source_access_required",
        "plan": {"goal": "secret", "source_ids": [1]},
    }
    module, _, _ = _load_workspace(monkeypatch, config=definition, result=None)

    response = await module.workspace_task(_request(), task_id=44)

    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["plan"] is None
    assert "secret" not in response.body.decode()
