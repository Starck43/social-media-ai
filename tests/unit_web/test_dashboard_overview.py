"""Isolated home contract checks; no application imports or database bootstrap.

Run only through Kilo/test-runner/pare-test with --noconftest. AST-loaded
functions check orchestration; owner acceptance still needs the real ASGI app.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, StrictUndefined, select_autoescape

ROOT = Path(__file__).resolve().parents[2]


def _functions(*names):
    tree = ast.parse((ROOT / "app/web/dashboard.py").read_text())
    nodes = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    for node in nodes:
        node.decorator_list = []
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
                              *nodes], type_ignores=[])
    scope = {}
    exec(compile(ast.fix_missing_locations(module), "isolated home functions", "exec"), scope)
    return scope


def _offer(*rights):
    scope = _functions("_offers")
    return scope["_offers"](SimpleNamespace(can=lambda model, action: (model, action) in rights))


def _render(offer, **extra):
    env = Environment(
        loader=ChoiceLoader([
            DictLoader({"web/base.html": "<html>{% block content %}{% endblock %}</html>"}),
            FileSystemLoader(ROOT / "app/web/templates"),
        ]),
        autoescape=select_autoescape(("html", "htm", "xml"), default_for_string=True),
        undefined=StrictUndefined,
    )
    env.filters["human_dt"] = lambda value, **kwargs: str(value or "")
    context = {
        "tenant": {"name": "Fixture workspace"},
        "can_open_chat": True,
        "is_superuser": False,
        "tenants": [],
        "filter_tenant_id": None,
        "next_step": None,
        "offer": offer,
        "recent_results": [],
        "recent_tasks": [],
        "effective_active": set(),
        "_tz": "Europe/Moscow",
        "request": SimpleNamespace(url=SimpleNamespace(path="/app/", query="")),
    }
    context.update(extra)
    return env.get_template("web/dashboard.html").render(**context)


def test_no_rights_do_not_render_injected_model_content():
    markup = _render(
        _offer(),
        recent_results=[{"id": 9, "title": "forbidden-analysis"}],
        recent_tasks=[{"name": "forbidden-task"}],
    )
    assert "forbidden-analysis" not in markup
    assert "forbidden-task" not in markup
    assert "data-home-tasks" not in markup
    assert "Для вашей роли просмотр результатов не разрешён" in markup
    assert 'href="/app/analytics"' not in markup
    assert 'href="/app/digests"' not in markup


def test_results_are_bounded_escaped_and_have_no_metric_sidebar():
    results = [
        {"id": i, "source_name": "hidden-source", "created_at": "fixture-time",
         "title": f"result-{i}<script>bad()</script>", "summary": "short result", "analysis_incomplete": False}
        for i in range(7)
    ]
    markup = _render(_offer(("aianalytics", "view")), recent_results=results)
    assert "result-4" in markup and "result-5" not in markup
    assert "<script>" not in markup and "&lt;script&gt;" in markup
    assert "hidden-source" not in markup
    for label in ("Быстрые действия", "Источники активны", "LLM-расход", "Топ тем", "Хэштеги", "Типы контента"):
        assert label not in markup


def test_task_preview_is_bounded_read_only_and_honestly_named():
    tasks = [
        {"id": i, "name": f"task-{i}", "next_run_at": None, "last_status": None}
        for i in range(5)
    ]
    markup = _render(_offer(("agenttask", "view")), recent_tasks=tasks, effective_active={0})
    assert "task-2" in markup and "task-3" not in markup
    assert "Недавно созданные" in markup
    assert "Время следующего запуска не определено" in markup
    assert "run-now" not in markup and "<table" not in markup


def test_digest_link_is_not_a_fake_digest_feed():
    markup = _render(_offer(("digestrun", "view")))
    assert 'href="/app/digests"' in markup
    assert "Дайджесты доступны в отдельном разделе" in markup
    assert 'href="/app/analytics"' not in markup


def test_operator_filter_remains_available_without_task_preview():
    markup = _render(_offer(), is_superuser=True, tenants=[{"id": 7, "name": "Workspace seven"}],
                     filter_tenant_id=7)
    assert 'name="tenant_id"' in markup
    assert 'value="7" selected' in markup
    assert "data-home-tasks" not in markup


@pytest.mark.asyncio
async def test_handler_skips_unauthorized_queries():
    scope = _functions("_offers", "dashboard")
    results = AsyncMock(side_effect=AssertionError("unauthorized analysis query"))
    tasks = AsyncMock(side_effect=AssertionError("unauthorized task query"))
    scope.update(
        _recent_results=results,
        _recent_tasks=tasks,
        _effective_active_ids=AsyncMock(return_value=set()),
        render=lambda request, template, **context: context,
    )
    request = SimpleNamespace(state=SimpleNamespace(
        tenant_id=7, web_user=SimpleNamespace(is_superuser=False), web_perms=None,
    ))
    context = await scope["dashboard"](request)
    assert context["recent_results"] == context["recent_tasks"] == []
    assert context["next_step"] is None
    results.assert_not_awaited()
    tasks.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_passes_operator_selected_workspace_and_caps_tasks():
    scope = _functions("_offers", "dashboard")
    results = AsyncMock(return_value=[])
    tasks = AsyncMock(return_value=[SimpleNamespace(id=i) for i in range(7)])
    scope.update(
        tenant_filter_context=AsyncMock(return_value=(11, [])),
        _recent_results=results,
        _recent_tasks=tasks,
        _effective_active_ids=AsyncMock(return_value=set()),
        render=lambda request, template, **context: context,
    )
    rights = {("aianalytics", "view"), ("agenttask", "view")}
    request = SimpleNamespace(state=SimpleNamespace(
        tenant_id=7,
        web_user=SimpleNamespace(is_superuser=True),
        web_perms=SimpleNamespace(can=lambda model, action: (model, action) in rights),
    ))
    context = await scope["dashboard"](request)
    results.assert_awaited_once_with(11, True, False)
    tasks.assert_awaited_once_with(7, True, 11)
    assert len(context["recent_tasks"]) == 3