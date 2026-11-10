"""P20-T11 task source routes, Today projection, and read-only presentation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape

from app.services.workspace.today import TodayProjection
from app.web import task_surface as routes

__source_only__ = True
ROOT = Path(__file__).resolve().parents[2]


class SourceRoutes:
    def __init__(self):
        self.connections_result = {"state": "available", "items": [], "selected_connection_id": None}
        self.projection_result = {"state": "not_configured", "items": [], "next_cursor": None}
        self.connections = AsyncMock(side_effect=lambda: self.connections_result)
        self.projection = AsyncMock(side_effect=lambda **kwargs: self.projection_result)
        self.configure = AsyncMock()


@pytest.fixture
async def source_client(monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    client_transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=client_transport, base_url="http://test")
    service = SourceRoutes()
    grants = {
        ("plannertask", routes.ActionType.VIEW),
        ("source", routes.ActionType.VIEW),
        ("source", routes.ActionType.CREATE),
    }
    monkeypatch.setattr(routes, "get_current_user", lambda: NS(id=8))
    monkeypatch.setattr(routes, "has_permission", lambda _actor, resource, action: (resource, action) in grants)
    monkeypatch.setattr(routes, "ensure_csrf", lambda _request, token: token == "ok")
    monkeypatch.setattr(
        routes,
        "render",
        lambda _request, template, **context: JSONResponse({"template": template, "context": context}),
    )
    monkeypatch.setattr("app.services.task_sources.task_source_service", service)
    yield NS(app=app, client=client, service=service, grants=grants)
    await client.aclose()


@pytest.mark.asyncio
async def test_sources_route_gates_before_listing_connections(source_client):
    source_client.grants.discard(("source", routes.ActionType.VIEW))

    response = await source_client.client.get("/task-hub/sources")

    assert response.status_code == 403
    source_client.service.connections.assert_not_awaited()
    source_client.service.projection.assert_not_awaited()


@pytest.mark.asyncio
async def test_sources_route_uses_selected_connection_and_passes_cursor(source_client):
    source_client.service.connections_result = {"state": "available", "items": [{"id": 3}], "selected_connection_id": 3}
    source_client.service.projection_result = {
        "state": "available",
        "items": [{"title": "Task"}],
        "next_cursor": "next",
    }

    response = await source_client.client.get("/task-hub/sources?cursor=next")

    assert response.status_code == 200
    assert response.json()["template"] == "web/task_sources.html"
    source_client.service.projection.assert_awaited_once_with(connection_id=3, cursor="next", limit=20)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "status"),
    [
        ({"_csrf": "bad", "enabled": "yes"}, 403),
        ({"_csrf": "ok", "enabled": "yes", "surprise": "field"}, 403),
        ([("_csrf", "ok"), ("enabled", "yes"), ("enabled", "no")], 403),
        ({"_csrf": "ok", "enabled": "maybe"}, 422),
    ],
)
async def test_source_consent_rejects_invalid_or_duplicate_forms_before_mutation(source_client, data, status):
    if isinstance(data, list):
        response = await source_client.client.post(
            "/task-hub/sources/3",
            content=urlencode(data),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    else:
        response = await source_client.client.post("/task-hub/sources/3", data=data)

    assert response.status_code == status
    source_client.service.configure.assert_not_awaited()


@pytest.mark.asyncio
async def test_source_consent_requires_both_planner_and_source_create_rights(source_client):
    source_client.grants.discard(("source", routes.ActionType.CREATE))

    response = await source_client.client.post("/task-hub/sources/3", data={"_csrf": "ok", "enabled": "yes"})

    assert response.status_code == 403
    source_client.service.configure.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("enabled", "selected"), [("yes", True), ("no", False)])
async def test_source_consent_post_calls_owner_service_and_never_requests_write(enabled, selected, source_client):
    response = await source_client.client.post("/task-hub/sources/3", data={"_csrf": "ok", "enabled": enabled})

    assert response.status_code == 303
    assert response.headers["location"] == "/app/task-hub/sources"
    source_client.service.configure.assert_awaited_once_with(3, enabled=enabled == "yes", selected=selected)


class FakeTodayPermissions:
    def __init__(self, allowed):
        self.allowed = set(allowed)
        self.workspace_role = "MEMBER"
        self.is_owner = False

    def can(self, resource, action="view"):
        return (resource, action) in self.allowed


class FakeTodaySourceRepository:
    def __init__(self, external):
        self.external = external
        self.requested_limit = None

    async def planner_tasks(self, _workspace, limit):
        return []

    async def external_tasks(self, _workspace, _user, limit):
        self.requested_limit = limit
        return self.external

    async def followups(self, _workspace, limit):
        return []

    async def upcoming_agent_tasks(self, _workspace, show_sources=False, limit=3):
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "caption"),
    [
        ("source_unavailable", "недоступ"),
        ("reauth_required", "повтор"),
        ("task_scope_required", "повтор"),
        ("rate_limited", "временно ограничил"),
    ],
)
async def test_today_keeps_unavailable_connected_task_source_visible(state, caption):
    repository = FakeTodaySourceRepository({"state": state, "items": [], "next_cursor": None})
    projection = TodayProjection(
        workspace_id=4,
        user_id=8,
        perms=FakeTodayPermissions({("plannertask", "view"), ("source", "view"), ("agenttask", "view")}),
        repository=repository,
        now=datetime(2026, 11, 11, 9, tzinfo=timezone.utc),
    )

    section = await projection.build_today_plan_section()

    item = next(row for row in section.items if row["kind"] == "external_task_source")
    assert item["stale_or_unavailable"] is True
    assert item["href"] == "/app/task-hub/sources"
    assert caption in item["title"].lower()
    assert repository.requested_limit == 5


@pytest.mark.asyncio
async def test_today_external_rows_are_bounded_and_keep_only_source_projection_fields():
    repository = FakeTodaySourceRepository(
        {
            "state": "available",
            "next_cursor": "unused",
            "items": [
                {
                    "source_ref": f"bitrix24:task:{task_id}",
                    "title": f"Provider task {task_id}",
                    "status": 3,
                    "connection_id": 3,
                    "due_at": "2026-11-11T12:00:00+03:00",
                    "url": "https://team.bitrix24.ru/company/personal/user/7/tasks/task/view/1/",
                    "body": "must not appear",
                    "tenant_id": 999,
                }
                for task_id in range(10)
            ],
        }
    )
    projection = TodayProjection(
        workspace_id=4,
        user_id=8,
        perms=FakeTodayPermissions({("plannertask", "view"), ("source", "view"), ("agenttask", "view")}),
        repository=repository,
        now=datetime(2026, 11, 11, 9, tzinfo=timezone.utc),
    )

    section = await projection.build_today_plan_section()

    external = [row for row in section.items if row["kind"] == "external_task"]
    assert len(external) == 5
    assert external[0]["title"] == "Provider task 0"
    assert external[0]["href"].startswith("https://team.bitrix24.ru/")
    assert "must not appear" not in repr(external)
    assert all(row["stale_or_unavailable"] is False for row in external)
    assert repository.requested_limit == 5


def _template_env():
    return Environment(
        loader=ChoiceLoader(
            [
                DictLoader(
                    {
                        "web/base.html": ("<html><body>{% block content %}{% endblock %}</body></html>"),
                        "web/_task_tabs.html": "<nav aria-label='Разделы задач'></nav>",
                    }
                ),
                FileSystemLoader(ROOT / "app/web/templates"),
            ]
        ),
        autoescape=select_autoescape(["html"]),
    )


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("available", "нет доступных вам задач"),
        ("source_unavailable", "записи не считаются пустыми"),
        ("reauth_required", "повторно подключить"),
        ("task_scope_required", "разрешить доступ к задачам"),
        ("not_configured", "разрешите чтение задач"),
    ],
)
def test_task_source_template_distinguishes_empty_unavailable_and_read_consent(state, expected):
    html = (
        _template_env()
        .get_template("web/task_sources.html")
        .render(
            tabs=[],
            active_tab="sources",
            csrf="token",
            sources={"items": [{"id": 3, "label": "Portal", "state": "verified", "enabled": True}]},
            projection={"state": state, "items": [], "next_cursor": None},
        )
    )

    assert expected in html.lower()
    assert "сейчас доступно только чтение" in html.lower()
    assert "добавить комментарий" not in html.lower()


def test_task_source_template_escapes_provider_text_and_opens_only_read_only_portal_link():
    html = (
        _template_env()
        .get_template("web/task_sources.html")
        .render(
            tabs=[],
            active_tab="sources",
            csrf="token",
            sources={"items": []},
            projection={
                "state": "available",
                "next_cursor": None,
                "items": [
                    {
                        "title": '<script id="xss">bad()</script>',
                        "status": 3,
                        "status_label": "В работе",
                        "connection_id": 3,
                        "due_at": "2026-11-11T12:00:00+03:00",
                        "url": "https://team.bitrix24.ru/task/7",
                        "stream": "assigned",
                        "read_only": True,
                    }
                ],
            },
        )
    )

    assert '<script id="xss">' not in html
    assert "&lt;script" in html
    assert 'target="_blank" rel="noopener noreferrer"' in html
    assert "read_only" not in html
