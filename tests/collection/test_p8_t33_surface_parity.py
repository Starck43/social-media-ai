"""P8-T33: all five surfaces expose the same VK search-category catalog.

The catalog is read-only registry metadata; every surface must serve
`category_catalog()` verbatim so web, API, CLI, admin and agent never disagree
about which categories exist, which evidence gates them, or how a status is
worded. No surface may present a `mocked` category as live-available.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from app.services.collection.search_categories import CATEGORY_PEOPLE, STATUS_MOCKED
from tests.shared.vk_search_contracts import no_external_search_io

__source_only__ = True
pytestmark = pytest.mark.usefixtures("no_external_search_io")


def _catalog_payload(payload):
    assert set(payload) == {"categories", "gate"}
    json.dumps(payload, ensure_ascii=False)
    return payload["categories"]


def test_api_route_serves_the_shared_catalog():
    from app.api.v1.endpoints import tasks as api_tasks

    paths = {getattr(route, "path", "") for route in api_tasks.router.routes}
    assert "/vk-search-categories" in paths
    assert "category_catalog" in inspect.getsource(api_tasks.get_vk_search_categories)


def test_web_route_serves_the_shared_catalog():
    from app.web import task_routes

    # The shared router carries prefix="/tasks", so the declared path is the
    # full one the browser and the editor's fetch actually use.
    paths = {getattr(route, "path", "") for route in task_routes.router.routes}
    assert "/tasks/vk-search-categories" in paths
    assert "category_catalog" in inspect.getsource(task_routes.web_vk_search_categories)


def test_web_editor_renders_the_catalog_read_only():
    import app.web

    template = (Path(app.web.__file__).parent / "templates" / "web" / "task_edit.html").read_text(encoding="utf-8")
    assert "/app/tasks/vk-search-categories" in template
    assert 'x-for="category in vkCategories"' in template
    assert "category.status_label" in template
    # The block is a status list, not a launcher: no form control writes a
    # category. Slice the block itself, not everything after its heading.
    block = template.split("Категории поиска ВКонтакте", 1)[1].split("</div>", 1)[0]
    assert "<input" not in block and "<select" not in block


def test_cli_command_serves_the_shared_catalog():
    from cli.commands import tasks as cli_tasks

    names = {getattr(command, "name", None) for command in cli_tasks.task_app.registered_commands}
    assert "vk-search-categories" in names
    assert "category_catalog" in inspect.getsource(cli_tasks)


def test_admin_view_serves_the_shared_catalog_to_an_operator():
    from app.admin.view_modules.operations import AgentTaskAdmin

    source = inspect.getsource(AgentTaskAdmin.vk_search_categories)
    assert "category_catalog" in source
    assert "get_admin_user" in source


@pytest.mark.asyncio
async def test_agent_tool_returns_the_shared_catalog_with_honest_status():
    from app.agent.tools import get_tool

    spec = get_tool("task_vk_search_categories")
    assert spec is not None, "task_vk_search_categories must be registered"
    assert spec.required_permission == "scheduling.task.view"

    payload = await spec.handler()
    categories = _catalog_payload(payload)
    people = next(entry for entry in categories if entry["id"] == CATEGORY_PEOPLE)
    assert people["status"] == STATUS_MOCKED
    assert people["status_label"]
    assert people["official_method"] == "users.search"
    # Mocked is implemented, not available: the tool must not advertise it.
    assert people["executable"] is False
    assert people["reason"]
