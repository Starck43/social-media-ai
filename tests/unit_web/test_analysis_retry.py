"""Remote no-DB checks: real pure projections/templates, AST-loaded mocked routes.

No application startup, provider calls, credentials, DB fixtures or ASGI claims.
Real transaction/permission/worker acceptance remains local through Kilo.
"""

from __future__ import annotations

import ast
import importlib.util
from contextlib import nullcontext
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[2]


def pure_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


projection = pure_module("stored_analysis", "app/services/ai/analysis_render.py")
retry = pure_module("staged_retry", "app/services/ai/analysis_retry.py")
activity = pure_module("source_activity", "app/jobs/source_activity.py")
START = "2026-09-10T12:30:00+00:00"
END = "2026-09-10T19:45:00+00:00"


def functions(path, *names):
    """Keep production control flow; replace only imports at app/DB boundaries."""
    tree = ast.parse((ROOT / path).read_text())

    class NoAppImports(ast.NodeTransformer):
        def visit_ImportFrom(self, node):
            return None if node.module and node.module.startswith("app.") else node

    nodes = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            nodes.append(NoAppImports().visit(node))
    assert len(nodes) == len(names)
    scope = {"Form": lambda default=None, **kw: default}
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), path, "exec"), scope)
    return scope


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"start_date": START},
        {"start_date": END, "end_date": START},
        {"start_date": False, "end_date": END},
        {"start_date": "bad", "end_date": END},
    ],
)
def test_retry_window_fails_closed(value):
    with pytest.raises(ValueError):
        retry.parse_staged_window(value)


def test_retry_window_keeps_exact_timestamps_and_single_instant():
    bounds = retry.parse_staged_window({"start_date": START, "end_date": END})
    assert bounds["start_date"].hour == 12
    assert bounds["end_date"].hour == 19
    assert bounds["start_date"].tzinfo == timezone.utc
    single = retry.parse_staged_window({"start_date": START, "end_date": START})
    assert single["start_date"] == single["end_date"]


@pytest.mark.parametrize("score", [None, 0, 0.8, 1, 2, True, float("nan")])
def test_source_row_uses_shared_sentiment_and_preserves_unknown(score):
    scope = functions("app/web/sources.py", "_analysis_row")
    scope["render_analysis"] = projection.render_analysis
    row = NS(
        id=1127,
        analysis_date=date(2026, 9, 10),
        main_topics=[],
        estimated_cost=None,
        llm_model=None,
        summary_data={"sentiment_score": score, "analysis_metadata": {"analysis_complete": False}},
    )
    result = scope["_analysis_row"](row)
    assert result["sentiment"] == projection.sentiment_summary(row.summary_data)["score"]
    assert result["analysis_incomplete"] is True
    assert result["cost_usd"] is None


class Response:
    def __init__(self, url, status_code=302):
        self.url, self.status_code = url, status_code


class Transaction:
    def __init__(self):
        self.outcome = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, kind, error, tb):
        self.outcome = "rollback" if kind else "commit"


def route_scope():
    scope = functions("app/web/analytics.py", "analytics_retry")
    row = NS(
        source_id=2939,
        source=NS(tenant_id=7),
        summary_data={"content_statistics": {"content_date_range": {"earliest": START, "latest": END}}},
    )
    manager = NS(select_related=Mock(), get=AsyncMock(return_value=row))
    manager.select_related.return_value = manager
    collected = NS(for_source=AsyncMock(return_value=[NS(id=4)]), reset_attempts=AsyncMock(return_value=1))
    jobs = NS(enqueue=AsyncMock(return_value=NS(id=20)))
    jobs.admit_source_job = jobs.enqueue
    transaction = Transaction()
    session = NS(begin=lambda: transaction, close=AsyncMock())
    factory = Mock(return_value=jobs)
    factory.lock_source_admission = AsyncMock()
    scope.update(
        SourceJobBusy=activity.SourceJobBusy,
        ensure_csrf=Mock(return_value=True),
        guard_web=Mock(return_value=None),
        add_flash=Mock(),
        RedirectResponse=Response,
        render=lambda *a, **kw: NS(status_code=kw["status_code"]),
        action_tenant_id=Mock(return_value=7),
        tenant_scope=lambda tid: nullcontext(),
        AIAnalytics=NS(objects=manager),
        CollectedItem=NS(objects=collected),
        JobManager=factory,
        new_session=lambda: session,
        render_analysis=projection.render_analysis,
        parse_staged_window=retry.parse_staged_window,
        _source_has_running_job=AsyncMock(return_value={"collect": False, "analyze": False}),
    )
    return scope, row, manager, collected, jobs, session, transaction


@pytest.mark.asyncio
async def test_detail_retry_passes_identical_window_to_read_reset_and_job():
    scope, row, manager, collected, jobs, session, tx = route_scope()
    response = await scope["analytics_retry"](NS(), 1127, token="fake", tenant_id=7)
    assert response.url == "/app/analytics/1127"
    manager.get.assert_awaited_once_with(id=1127, tenant_id=7)
    bounds = retry.parse_staged_window({"start_date": START, "end_date": END})
    collected.for_source.assert_awaited_once_with(2939, limit=1, include_exhausted=True, **bounds)
    collected.reset_attempts.assert_awaited_once_with(session, 2939, **bounds)
    args = jobs.enqueue.await_args.kwargs
    assert args == {
        "job_type": "analyze",
        "tenant_id": 7,
        "session": session,
        "payload": {"source_ids": [2939], "staged_window": {"start_date": START, "end_date": END}},
    }
    assert tx.outcome == "commit"
    session.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "blocked",
    ["csrf", "view", "analyze", "tenant", "missing", "foreign-source", "window", "empty", "pending", "running"],
)
async def test_detail_retry_blockers_never_write(blocked):
    scope, row, manager, collected, jobs, session, tx = route_scope()
    if blocked == "csrf":
        scope["ensure_csrf"].return_value = False
    elif blocked in ("view", "analyze"):
        scope["guard_web"].side_effect = lambda req, model, action, **kw: (
            Response("denied") if action == blocked else None
        )
    elif blocked == "tenant":
        scope["action_tenant_id"].return_value = None
    elif blocked == "missing":
        manager.get.return_value = None
    elif blocked == "foreign-source":
        row.source.tenant_id = 8
    elif blocked == "window":
        row.summary_data = {}
    elif blocked == "empty":
        collected.for_source.return_value = []
    else:
        scope["_source_has_running_job"].return_value = {
            "collect": blocked == "pending",
            "analyze": blocked == "running",
        }
    await scope["analytics_retry"](NS(), 1127, token="fake", tenant_id=7)
    collected.reset_attempts.assert_not_awaited()
    jobs.enqueue.assert_not_awaited()
    assert tx.outcome is None


@pytest.mark.asyncio
async def test_enqueue_failure_rolls_back_reset_and_never_reports_success():
    scope, _, _, _, jobs, session, tx = route_scope()
    jobs.enqueue.side_effect = RuntimeError("synthetic queue failure")
    with pytest.raises(RuntimeError):
        await scope["analytics_retry"](NS(), 1127, token="fake", tenant_id=7)
    assert tx.outcome == "rollback"
    session.close.assert_awaited_once()
    assert not scope["add_flash"].called


@pytest.mark.asyncio
async def test_pending_and_running_jobs_are_both_considered():
    scope = functions("app/web/sources.py", "_source_has_running_job")
    query = AsyncMock(return_value={"collect": False, "analyze": True})
    scope["JobManager"] = lambda: NS(source_activity=query)
    assert await scope["_source_has_running_job"](2939) == {"collect": False, "analyze": True}
    query.assert_awaited_once_with(2939)


def template_context(**overrides):
    context = dict(
        display=projection.render_analysis({}),
        analysis=NS(
            id=1127,
            source=NS(name="<script>bad</script>"),
            source_id=2939,
            tenant_id=7,
            analysis_date=date(2026, 9, 10),
        ),
        retry_needed=True,
        can_retry=True,
        retry_available=True,
        retry_running=False,
        retry_window_known=True,
        is_superuser=False,
        csrf="fixture-only",
        perms=NS(can=lambda *args: True),
        chain=[],
        chain_url=None,
        back_url="/app",
        back_label="На главную",
        platform_name="VK",
        window_start=None,
        window_end=None,
        analyzed_at=None,
        vk_reactions=False,
        topics=[],
        _tz="UTC",
    )
    context.update(overrides)
    return context


def render_detail(**overrides):
    env = Environment(
        loader=ChoiceLoader(
            [
                DictLoader({"web/base.html": "{% block content %}{% endblock %}"}),
                FileSystemLoader(ROOT / "app/web/templates"),
            ]
        ),
        autoescape=select_autoescape(),
    )
    env.filters["ui_label"] = str
    env.filters["human_dt"] = lambda value, **kw: str(value or "")
    return env.get_template("web/analytics_detail.html").render(**template_context(**overrides))


def test_retry_template_reuses_post_csrf_confirmation_and_existing_button_style():
    html = render_detail(is_superuser=True, platform_name="<script>bad</script>")
    assert 'action="/app/analytics/1127/retry"' in html
    assert 'method="post"' in html and 'name="_csrf" value="fixture-only"' in html
    assert 'name="tenant_id" value="7"' in html
    assert 'data-confirm-title="Повторить анализ?"' in html
    assert "bg-violet-500" in html
    assert "не пересобирает эту запись целиком" in html
    assert "&lt;script&gt;bad&lt;/script&gt;" in html
    assert "<script>bad</script>" not in html


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"can_retry": False}, None),
        ({"retry_needed": False}, None),
        ({"retry_running": True}, "уже в очереди"),
        ({"retry_available": False}, "нет оставшихся сырых материалов"),
        ({"retry_available": False, "retry_window_known": False}, "Период исходных материалов не сохранён"),
    ],
)
def test_retry_template_only_offers_action_when_appropriate(overrides, message):
    html = render_detail(**overrides)
    assert 'action="/app/analytics/1127/retry"' not in html
    if message:
        assert message in html


def test_worker_uses_strict_retry_bounds_before_staged_selection():
    tree = ast.parse((ROOT / "app/jobs/handlers.py").read_text())
    handler = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "handle_analyze"
    )
    override = next(
        node
        for node in handler.body
        if isinstance(node, ast.If) and ast.unparse(node.test) == "'staged_window' in payload"
    )
    code = ast.unparse(override)
    assert "parse_staged_window(payload['staged_window'])" in code
    assert handler.body.index(override) < next(i for i, node in enumerate(handler.body) if isinstance(node, ast.For))
    # Invalid bounds raise, rather than falling back to the CLI/all-history window.
    assert not any(isinstance(node, ast.Try) for node in ast.walk(override))


@pytest.mark.asyncio
@pytest.mark.parametrize("bounded", [True, False])
async def test_reset_attempts_keeps_tenant_source_and_optional_timestamp_scope(bounded):
    scope = functions("app/models/managers/collected_item_manager.py", "reset_attempts")
    scope.update(
        sa_text=lambda text: text,
        settings=NS(DB_SCHEMA="fixture_schema"),
        _staged_mutation_scope=lambda: (" AND tenant_id = :tenant_id", {"tenant_id": 7}),
    )
    session = NS(execute=AsyncMock(return_value=NS(rowcount=3)))
    bounds = retry.parse_staged_window({"start_date": START, "end_date": END}) if bounded else {}
    assert await scope["reset_attempts"](None, session, 2939, **bounds) == 3
    sql, params = session.execute.await_args.args
    assert "source_id = :source_id" in sql and "tenant_id = :tenant_id" in sql
    assert params == {"source_id": 2939, "tenant_id": 7, **bounds}
    assert ("published_at >= :start_date" in sql) == bounded
    assert ("published_at <= :end_date" in sql) == bounded


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["enqueue", "empty", "busy", "failure"])
async def test_source_retry_reuses_configured_period_and_atomic_queue(state):
    original, _, _, collected, jobs, session, tx = route_scope()
    scope = functions("app/web/sources.py", "source_analyze_now")
    scope.update(original)
    source = NS(id=2939, tenant_id=7, name="Fixture")
    scope.update(
        Source=NS(objects=NS(get=AsyncMock(return_value=source))),
        AgentTaskManager=NS(parse_date=lambda raw: datetime.fromisoformat(raw).date() if raw else None),
        _resolve_source_window=AsyncMock(
            return_value={"cli_dates": {"start_date": "2026-09-10", "end_date": "2026-10-10"}}
        ),
    )
    if state == "empty":
        collected.for_source.return_value = []
    elif state == "busy":
        scope["_source_has_running_job"].return_value = {"collect": False, "analyze": True}
    elif state == "failure":
        jobs.enqueue.side_effect = RuntimeError("synthetic queue failure")
    if state == "failure":
        with pytest.raises(RuntimeError):
            await scope["source_analyze_now"](NS(), 2939, token="fake", tenant_id=7)
        assert tx.outcome == "rollback"
        assert not scope["add_flash"].called
    else:
        response = await scope["source_analyze_now"](NS(), 2939, token="fake", tenant_id=7)
        assert response.url == "/app/sources/2939"
    if state in ("empty", "busy"):
        jobs.enqueue.assert_not_awaited()
        collected.reset_attempts.assert_not_awaited()
    if state == "enqueue":
        assert tx.outcome == "commit"
        bounds = {"start_date": date(2026, 9, 10), "end_date": date(2026, 10, 10)}
        collected.for_source.assert_awaited_once_with(2939, limit=1, include_exhausted=True, **bounds)
        collected.reset_attempts.assert_awaited_once_with(session, 2939, **bounds)
        assert jobs.enqueue.await_args.kwargs["session"] is session


def source_html(rights=True, **overrides):
    env = Environment(
        loader=ChoiceLoader(
            [
                DictLoader({"web/base.html": "{% block content %}{% endblock %}"}),
                FileSystemLoader(ROOT / "app/web/templates"),
            ]
        ),
        autoescape=select_autoescape(),
    )
    env.filters["ui_label"] = str
    env.filters["human_dt"] = lambda value, **kw: str(value or "")
    source = NS(
        id=2939,
        tenant_id=7,
        name="Fixture",
        platform=NS(name="VK", id=1),
        source_type=NS(display_name="User", db_value="user"),
        external_id="123",
        params={},
        is_active=True,
    )
    context = dict(
        source=source,
        perms=NS(can=lambda *args: rights),
        running={"collect": False, "analyze": False},
        csrf="fake",
        is_superuser=False,
        can_delete=False,
        analytics_count=1,
        usage_count=0,
        mode=NS(label="Pull", hint="Fixture", is_push=True),
        monitored_users=[],
        schedules=[],
        sentiment_label="—",
        cost_usd=0,
        last_checked_label="—",
        is_ready=True,
        platforms=[],
        source_types=[],
        pending_count=3,
        given_up=1,
        given_up_label="запись",
        collections=[],
        request=NS(url=NS(path="/app/sources/2939", query="")),
        recent=[
            dict(
                id=1127,
                analysis_date="10.09.2026",
                main_topics=[],
                sentiment=None,
                llm_model=None,
                cost_usd=None,
                analysis_incomplete=True,
            )
        ],
    )
    context.update(overrides)
    return env.get_template("web/source_detail.html").render(**context)


def test_source_exposes_backlog_incomplete_badge_and_retry():
    html = source_html()
    assert "data-analysis-backlog" in html
    assert 'data-coverage="incomplete"' in html
    assert "Повторить анализ" in html
    assert "Автоматические попытки для них приостановлены" in html


def test_source_hides_all_analysis_buttons_without_permission():
    html = source_html(rights=False)
    assert 'action="/app/sources/2939/analyze"' not in html
    assert "data-analysis-backlog" in html  # read-only explanation remains


def test_source_hides_retry_during_collection_or_queued_analysis():
    for running in ({"collect": True, "analyze": False}, {"collect": False, "analyze": True}):
        html = source_html(running=running)
        assert 'action="/app/sources/2939/analyze"' not in html


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "function,path", [("analytics_retry", "app/web/analytics.py"), ("source_analyze_now", "app/web/sources.py")]
)
async def test_atomic_busy_conflict_rolls_back_attempt_reset_and_reports_info(function, path):
    original, _, _, collected, jobs, session, tx = route_scope()
    jobs.enqueue.side_effect = activity.SourceJobBusy(NS(id=99, status="pending"))
    if function == "analytics_retry":
        scope = original
        await scope[function](NS(), 1127, token="fake", tenant_id=7)
    else:
        scope = functions(path, function)
        scope.update(original)
        scope.update(
            Source=NS(objects=NS(get=AsyncMock(return_value=NS(id=2939, tenant_id=7, name="Fixture")))),
            AgentTaskManager=NS(parse_date=lambda value: None),
            _resolve_source_window=AsyncMock(return_value={}),
        )
        await scope[function](NS(), 2939, token="fake", tenant_id=7)
    scope["JobManager"].lock_source_admission.assert_awaited_once_with(session, 7)
    collected.reset_attempts.assert_awaited_once()
    assert tx.outcome == "rollback"
    assert all(call.args[1] == "info" for call in scope["add_flash"].call_args_list)
