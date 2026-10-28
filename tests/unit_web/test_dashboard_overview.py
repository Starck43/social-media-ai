"""Isolated home contract checks; no application imports or database bootstrap.

Normal local execution follows Kilo/test-runner/pare-test with --noconftest.
Remote sandbox checks use the skill's no-DB exception and never access DB.
AST-loaded functions check orchestration; acceptance still needs the real ASGI app.
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
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    for node in nodes:
        node.decorator_list = []

        # Inject the result renderer in these isolated checks, never import app
        # services (which would initialize application dependencies).
        class ModelImports(ast.NodeTransformer):
            def visit_ImportFrom(self, child):
                if child.module in (
                    "app.services.ai.analysis_render",
                    "app.services.digest.checkpoints",
                    "app.services.digest.delivery_outcomes",
                    "app.models.analysis.ai_analytics",
                    "app.models.collection.collected_item",
                    "app.models.notifications.digest_run",
                    "app.models.scheduling.job",
                    "app.models.managers.agent_task_manager",
                    "app.core.tenant_context",
                ):
                    return None
                return child

        ModelImports().visit(node)
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes],
        type_ignores=[],
    )
    scope = {}
    exec(compile(ast.fix_missing_locations(module), "isolated home functions", "exec"), scope)
    return scope


def _offer(*rights):
    scope = _functions("_offers")
    return scope["_offers"](SimpleNamespace(can=lambda model, action: (model, action) in rights))


def _render(offer, *, real_base=False, **extra):
    env = Environment(
        loader=ChoiceLoader(
            [
                DictLoader(
                    {}
                    if real_base
                    else {
                        "web/base.html": "<html>{% block page_actions %}{% endblock %}{% block content %}{% endblock %}</html>",
                    }
                ),
                FileSystemLoader(ROOT / "app/web/templates"),
            ]
        ),
        autoescape=select_autoescape(("html", "htm", "xml"), default_for_string=True),
        undefined=StrictUndefined,
    )
    env.filters["ui_label"] = str
    env.filters["human_dt"] = lambda value, **kwargs: str(value or "")
    env.globals["static_v"] = lambda path: "/static/" + path
    env.globals["url_for"] = lambda name, path: "/static/" + path
    context = {
        "tenant": {"id": 7, "name": "Fixture workspace"},
        "user": SimpleNamespace(username="fixture", role=None, has_admin_access=lambda: False),
        "section": "dashboard",
        "nav": [],
        "mobile_nav": [],
        "workspaces": [],
        "connection_alerts": [],
        "unread_notifications": 0,
        "flashes": [],
        "csrf": "fixture-only",
        "can_open_chat": True,
        "is_superuser": False,
        "tenants": [],
        "filter_tenant_id": None,
        "next_step": None,
        "offer": offer,
        "recent_results": [],
        "recent_tasks": [],
        "source_schedule_status_unknown": False,
        "pipeline": None,
        "_tz": "Europe/Moscow",
        "request": SimpleNamespace(url=SimpleNamespace(path="/app/", query=""), query_params={}),
        "perms": SimpleNamespace(can_manage_workspace=lambda target: False, is_owner=False, workspace_role="VIEWER"),
        "member_level": None,
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
        {
            "id": i,
            "source_name": "hidden-source",
            "created_at": "fixture-time",
            "title": f"result-{i}<script>bad()</script>",
            "summary": "short result",
            "analysis_incomplete": False,
        }
        for i in range(7)
    ]
    for result in results:
        result.update(kind="analysis", href=f"/app/analytics/{result['id']}", period_label=None, delivery_status=None)
    markup = _render(_offer(("aianalytics", "view")), recent_results=results)
    assert "result-4" in markup and "result-5" not in markup
    assert "<script>" not in markup and "&lt;script&gt;" in markup
    assert "hidden-source" not in markup
    for label in ("Быстрые действия", "Источники активны", "LLM-расход", "Топ тем", "Хэштеги", "Типы контента"):
        assert label not in markup


def test_task_preview_is_bounded_read_only_and_honestly_named():
    tasks = [
        {"id": i, "name": f"task-{i}", "next_run_at": "fixture-time", "is_due": i == 0, "last_status": None}
        for i in range(5)
    ]
    markup = _render(_offer(("agenttask", "view")), recent_tasks=tasks)
    assert "task-2" in markup and "task-3" not in markup
    assert "Ближайшие запуски" in markup
    assert "Ожидает запуска" in markup and "Следующий запуск: fixture-time" in markup
    assert "run-now" not in markup and "<table" not in markup


def test_digest_link_is_not_a_fake_digest_feed():
    results = [
        {
            "id": 4,
            "kind": "digest",
            "href": "/app/digests",
            "created_at": "created",
            "title": "Дайджест за период",
            "summary": "summary",
            "source_name": None,
            "analysis_incomplete": False,
            "period_label": "Период 01.10 — 02.10",
            "delivery_status": "Доставлен частично",
        }
    ]
    markup = _render(_offer(("digestrun", "view")), recent_results=results)
    assert 'href="/app/digests"' in markup
    assert "Доставлен частично" in markup and "Период 01.10 — 02.10" in markup
    assert 'href="/app/analytics"' not in markup


def test_platform_admin_does_not_get_workspace_filter_in_dashboard():
    markup = _render(_offer(), is_superuser=False, tenants=[{"id": 7, "name": "Workspace seven"}], filter_tenant_id=7)
    assert 'name="tenant_id"' not in markup
    assert 'value="7" selected' not in markup
    assert "data-home-tasks" not in markup


def test_real_base_and_partials_render_with_home_only_style_scope():
    markup = _render(_offer(), real_base=True)
    assert 'data-shell-section="dashboard"' in markup
    assert 'class="ui-home"' in markup
    assert 'id="ui-confirm"' in markup
    assert "Последние результаты" in markup


def test_home_shell_class_is_not_added_to_other_sections():
    markup = _render(_offer(), real_base=True, section="sources")
    assert 'data-shell-section="sources"' in markup


@pytest.mark.asyncio
@pytest.mark.parametrize("show_sources", [False, True])
async def test_result_query_only_loads_source_metadata_with_view_right(show_sources):
    scope = _functions("_recent_results_in_scope")
    calls = []

    class Analysis(SimpleNamespace):
        @property
        def source(self):
            assert show_sources, "source metadata accessed without source.view"
            return SimpleNamespace(name="fixture-source")

    class Query:
        def filter(self, **kwargs):
            calls.append(("filter", kwargs))
            return self

        def select_related(self, value):
            calls.append(("relation", value))
            return self

        def order_by(self, *values):
            return self

        async def limit(self, value):
            assert value == 5
            return [Analysis(id=9, created_at="fixture-time", summary_data={})]

    scope.update(
        AIAnalytics=SimpleNamespace(
            objects=Query(),
            created_at=SimpleNamespace(desc=lambda: "created-desc"),
            id=SimpleNamespace(desc=lambda: "id-desc"),
        ),
        render_analysis=lambda data, source_name: {
            "display_title": "Fixture result",
            "analysis_summary": "x" * 300,
            "coverage": {"incomplete": True},
        },
    )
    results = await scope["_recent_results_in_scope"](7, show_sources)
    assert ("filter", {"tenant_id": 7}) in calls
    assert (("relation", "source") in calls) == show_sources
    assert results[0]["source_name"] == ("fixture-source" if show_sources else None)
    assert len(results[0]["summary"]) == 220
    assert results[0]["analysis_incomplete"]


def test_mixed_feed_is_newest_first_stable_and_limited_after_merge():
    from datetime import datetime, timezone

    scope = _functions("_merge_result_feed")
    now = datetime(2026, 10, 16, tzinfo=timezone.utc)
    analyses = [{"id": i, "kind": "analysis", "created_at": now} for i in range(1, 5)]
    digests = [{"id": i, "kind": "digest", "created_at": now} for i in range(1, 4)]

    merged = scope["_merge_result_feed"](analyses, digests)

    assert len(merged) == 5
    assert [(row["kind"], row["id"]) for row in merged] == [
        ("digest", 3),
        ("digest", 2),
        ("digest", 1),
        ("analysis", 4),
        ("analysis", 3),
    ]


def test_digest_delivery_status_uses_ledger_and_reports_missing_evidence_as_unknown():
    from app.services.digest.checkpoints import new_checkpoint, transition, validate_checkpoint
    from app.services.digest.delivery_outcomes import summarize_delivery

    scope = _functions("_digest_delivery_status")
    scope["validate_checkpoint"] = validate_checkpoint
    scope["summarize_delivery"] = summarize_delivery
    checkpoint = new_checkpoint(
        run_id=1,
        tenant_id=7,
        generation="fixture-generation",
        content="fixture digest",
        splitter="fixture-splitter",
        targets=[
            {"binding_id": 1, "channel": "telegram", "destination_id": "1", "parts": ["first"]},
            {"binding_id": 2, "channel": "max", "destination_id": "2", "parts": ["second"]},
        ],
    )
    pending_run = SimpleNamespace(id=1, tenant_id=7, delivery_state=checkpoint, status="pending", error="")
    assert scope["_digest_delivery_status"](pending_run) == "pending"

    checkpoint = transition(checkpoint, 0, 0, "in_flight")
    checkpoint = transition(checkpoint, 0, 0, "sent", message_id="message-1")
    checkpoint = transition(checkpoint, 1, 0, "in_flight")
    checkpoint = transition(checkpoint, 1, 0, "rejected")
    run = SimpleNamespace(id=1, tenant_id=7, delivery_state=checkpoint, status="failed", error="")
    assert scope["_digest_delivery_status"](run) == "partial"

    run.id = 2
    assert scope["_digest_delivery_status"](run) == "unknown"
    run.id = 1
    run.tenant_id = 8
    assert scope["_digest_delivery_status"](run) == "unknown"
    run.tenant_id = 7

    run.delivery_state = {"broken": True}
    assert scope["_digest_delivery_status"](run) == "unknown"

    run.delivery_state = None
    run.status = "failed"
    run.error = "other failure"
    assert scope["_digest_delivery_status"](run) == "failed"
    run.status = "unrecognized"
    assert scope["_digest_delivery_status"](run) == "unknown"


@pytest.mark.asyncio
async def test_handler_skips_unauthorized_queries():
    scope = _functions("_offers", "dashboard")
    results = AsyncMock(side_effect=AssertionError("unauthorized analysis query"))
    digests = AsyncMock(side_effect=AssertionError("unauthorized digest query"))
    tasks = AsyncMock(side_effect=AssertionError("unauthorized task query"))
    scope.update(
        _recent_results=results,
        _recent_digests=digests,
        _merge_result_feed=lambda analyses, digest_rows: [*analyses, *digest_rows],
        _recent_tasks=tasks,
        _pipeline=AsyncMock(return_value=None),
        render=lambda request, template, **context: context,
    )
    request = SimpleNamespace(
        state=SimpleNamespace(
            tenant_id=7,
            web_user=SimpleNamespace(is_active=True, is_platform_admin=False),
            web_perms=None,
        )
    )
    context = await scope["dashboard"](request)
    assert context["recent_results"] == context["recent_tasks"] == []
    assert context["next_step"] is None
    results.assert_not_awaited()
    digests.assert_not_awaited()
    tasks.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_ignores_platform_admin_workspace_selection_and_uses_active_scope():
    scope = _functions("_offers", "dashboard")
    results = AsyncMock(return_value=[])
    digests = AsyncMock(return_value=[])
    tasks = AsyncMock(return_value=[SimpleNamespace(id=i) for i in range(3)])
    scope.update(
        tenant_filter_context=AsyncMock(return_value=(11, [])),
        _recent_results=results,
        _recent_digests=digests,
        _merge_result_feed=lambda analyses, digest_rows: [*analyses, *digest_rows],
        _recent_tasks=tasks,
        _pipeline=AsyncMock(return_value=None),
        render=lambda request, template, **context: context,
    )
    rights = {("aianalytics", "view"), ("agenttask", "view")}
    request = SimpleNamespace(
        state=SimpleNamespace(
            tenant_id=7,
            web_user=SimpleNamespace(is_platform_admin=True),
            web_perms=SimpleNamespace(can=lambda model, action: (model, action) in rights),
        )
    )
    context = await scope["dashboard"](request)
    results.assert_awaited_once_with(7, False, False)
    digests.assert_not_awaited()
    tasks.assert_awaited_once_with(7, False, None, show_sources=False)
    assert len(context["recent_tasks"]) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rights", "expect_analyses", "expect_digests"),
    [
        ({("aianalytics", "view")}, True, False),
        ({("digestrun", "view")}, False, True),
        ({("aianalytics", "view"), ("digestrun", "view")}, True, True),
    ],
)
async def test_analysis_and_digest_reads_have_independent_view_gates(rights, expect_analyses, expect_digests):
    scope = _functions("_offers", "dashboard")
    analyses = AsyncMock(return_value=[])
    digests = AsyncMock(return_value=[])
    scope.update(
        _recent_results=analyses,
        _recent_digests=digests,
        _merge_result_feed=lambda analysis_rows, digest_rows: [*analysis_rows, *digest_rows],
        _recent_tasks=AsyncMock(return_value=[]),
        _pipeline=AsyncMock(return_value=None),
        render=lambda request, template, **context: context,
    )
    request = SimpleNamespace(
        state=SimpleNamespace(
            tenant_id=7,
            web_user=SimpleNamespace(is_platform_admin=False),
            web_perms=SimpleNamespace(can=lambda model, action: (model, action) in rights),
        )
    )

    await scope["dashboard"](request)

    assert analyses.await_count == int(expect_analyses)
    assert digests.await_count == int(expect_digests)


class _CountQuery:
    """Minimal chainable stand-in: filter/order_by/limit recorded, await yields rows."""

    def __init__(self, count=0, rows=()):
        self.count_value = count
        self.rows = list(rows)
        self.filters = []

    def filter(self, *args, **kwargs):
        self.filters.append((args, kwargs))
        return self

    def order_by(self, *args):
        return self

    def limit(self, value):
        assert value == 3
        return self

    async def count(self):
        return self.count_value

    def __await__(self):
        async def _await_rows():
            return list(self.rows)

        return _await_rows().__await__()


def _with_models(staged=0, analyzed=0, jobs=()):
    import types

    AIAnalytics = types.SimpleNamespace(objects=_CountQuery(analyzed))
    CollectedItem = types.SimpleNamespace(objects=_CountQuery(staged))
    Job = types.SimpleNamespace(
        objects=_CountQuery(rows=jobs),
        created_at=types.SimpleNamespace(desc=lambda: "created-desc"),
        id=types.SimpleNamespace(desc=lambda: "id-desc"),
    )
    return {"AIAnalytics": AIAnalytics, "CollectedItem": CollectedItem, "Job": Job}


@pytest.mark.asyncio
async def test_pipeline_hidden_without_component_view_rights():
    scope = _functions("_pipeline")
    scope.update(_with_models())
    assert await scope["_pipeline"](7, False, _offer()) is None


@pytest.mark.asyncio
async def test_pipeline_superuser_without_workspace_sees_global_hint():
    scope = _functions("_pipeline")
    scope.update(_with_models())
    assert await scope["_pipeline"](None, True, _offer(("aianalytics", "view"))) == {"scope_all": True}


@pytest.mark.asyncio
async def test_pipeline_counts_staged_analyzed_and_running_jobs():
    running = SimpleNamespace(status="running", job_type="collect")
    queued = SimpleNamespace(status="pending", job_type="analyze")
    stubs = _with_models(staged=4, analyzed=9, jobs=(running, queued))
    scope = _functions("_pipeline")
    scope.update(stubs)
    result = await scope["_pipeline"](
        7, False, _offer(("aianalytics", "view"), ("collecteditem", "view"), ("job", "view"))
    )
    assert result["staged"] == 4 and result["analyzed"] == 9
    assert result["running"] == [{"job_type": "collect", "label": "Сбор данных выполняется…"}]
    assert result["queued_count"] == 1 and result["job_count"] == 2
    assert any(kwargs.get("tenant_id") == 7 for _, kwargs in stubs["CollectedItem"].objects.filters)
    assert any(kwargs.get("tenant_id") == 7 for _, kwargs in stubs["Job"].objects.filters)


def test_pipeline_strip_shows_waiting_and_model_activity():
    markup = _render(
        _offer(("aianalytics", "view"), ("collecteditem", "view"), ("job", "view")),
        pipeline={
            "scope_all": False,
            "staged": 4,
            "analyzed": 9,
            "running": [{"job_type": "analyze", "label": "Анализ выполняется…"}],
            "preview_limit": 3,
            "queued_count": 0,
            "job_count": 1,
        },
    )
    assert "data-home-pipeline" in markup
    assert "сохранено материалов" in markup
    assert "сохранено анализов" in markup
    assert "Анализ выполняется…" in markup
