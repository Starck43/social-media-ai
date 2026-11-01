"""P8-T46–T49: execute the five read-only catalog handlers without network/DB.

Handler-level parity, not HTTP/session authentication or live-provider evidence.
Web authorization and the operator identity are explicit in-memory doubles.
"""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from app.services.collection.search_categories import category_catalog
from tests.shared.vk_search_contracts import no_external_search_io

__source_only__ = True
pytestmark = pytest.mark.usefixtures("no_external_search_io")


def request():
    return Request({"type": "http", "method": "GET", "path": "/vk-search-categories", "headers": []})


@pytest.mark.asyncio
async def test_all_five_handlers_return_identical_catalog_and_rights_gates(monkeypatch, capsys):
    from app.admin.view_modules.operations import AgentTaskAdmin
    from app.agent.tools import get_tool
    from app.api.v1.endpoints import tasks as api_tasks
    from app.web import task_routes
    from cli.commands import tasks as cli_tasks
    from cli.commands.task_output import task_output_format

    guard_calls = []

    def allow(req, model, action, *, back):
        guard_calls.append((model, action, back))
        return None

    monkeypatch.setattr(task_routes, "guard_web", allow)
    admin = SimpleNamespace(is_accessible=lambda req: True, get_admin_user=lambda req: SimpleNamespace(id=42))
    tool = get_tool("task_vk_search_categories")
    assert tool is not None and tool.required_permission == "scheduling.task.view"
    with task_output_format("human"):
        cli_payload = cli_tasks.task_vk_search_categories_cmd()
    capsys.readouterr()
    payloads = {
        "api": await api_tasks.get_vk_search_categories(_user=SimpleNamespace(id=42)),
        "web": await task_routes.web_vk_search_categories(request()),
        "cli": cli_payload,
        "admin": json.loads((await AgentTaskAdmin.vk_search_categories(admin, request())).body),
        "agent": await tool.handler(),
    }
    expected = {"categories": category_catalog(), "gate": "docs/tasks/q-5.md"}
    for surface, payload in payloads.items():
        assert payload == expected, surface
        entries = {entry["id"]: entry for entry in payload["categories"]}
        for category in ("video", "apps", "market"):
            assert entries[category]["status"] == "mocked", surface
            assert entries[category]["reason"] == "vk_method_profile_unverified", surface
        assert entries["apps"]["official_method"] == "apps.getCatalog", surface
        assert entries["music"]["status"] == "deferred", surface
        assert entries["music"]["reason"] == "separate_permission_evidence_required", surface
        assert not any(entry["executable"] for entry in entries.values()), surface
    assert guard_calls == [("agenttask", "view", "/app/tasks")]


@pytest.mark.asyncio
async def test_web_catalog_preserves_denial_without_returning_metadata(monkeypatch):
    from app.web import task_routes

    denied = Response(status_code=403)
    monkeypatch.setattr(task_routes, "guard_web", lambda *args, **kwargs: denied)
    assert await task_routes.web_vk_search_categories(request()) is denied


@pytest.mark.parametrize("actor", [None, SimpleNamespace(id=None), SimpleNamespace(id=True)])
@pytest.mark.asyncio
async def test_admin_catalog_requires_a_numeric_authenticated_actor(actor):
    from app.admin.view_modules.operations import AgentTaskAdmin

    admin = SimpleNamespace(is_accessible=lambda req: True, get_admin_user=lambda req: actor)
    with pytest.raises(HTTPException) as caught:
        await AgentTaskAdmin.vk_search_categories(admin, request())
    assert caught.value.status_code == 403
