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
PROGRESS_MACRO = (
    "{% macro progress_bar(label, state='unknown', percent=none, processed=none, total=none, note=none) %}"
    "<div>{{ label }}</div>{% endmacro %}"
)


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
        id=1127,
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
        launch_job_background=Mock(),
        get_current_user=lambda: NS(id=81),
        AgentScenarioManager=lambda: NS(get=AsyncMock(return_value=None)),
    )
    return scope, row, manager, collected, jobs, session, transaction


@pytest.mark.asyncio
async def test_detail_retry_passes_identical_window_to_read_reset_and_job():
    scope, row, manager, collected, jobs, session, tx = route_scope()
    launch_observed = []
    scope["launch_job_background"].side_effect = lambda _job_id, **_kwargs: launch_observed.append(tx.outcome)
    response = await scope["analytics_retry"](
        NS(state=NS(web_user=NS(id=81))), 1127, token="fake", tenant_id=7
    )
    assert response.url == "/app/analytics/1127?retry_job_id=20"
    manager.get.assert_awaited_once_with(id=1127, tenant_id=7)
    bounds = retry.parse_staged_window({"start_date": START, "end_date": END})
    collected.for_source.assert_awaited_once_with(2939, limit=1, include_exhausted=True, **bounds)
    collected.reset_attempts.assert_awaited_once_with(session, 2939, **bounds)
    args = jobs.enqueue.await_args.kwargs
    assert args == {
        "job_type": "analyze",
        "tenant_id": 7,
        "session": session,
        "payload": {
            "source_ids": [2939],
            "staged_window": {"start_date": START, "end_date": END},
            "retry_analytics_id": 1127,
        },
        "initiated_by_user_id": 81,
    }
    assert args["payload"]["retry_analytics_id"] == 1127
    assert tx.outcome == "commit"
    session.close.assert_awaited_once()
    scope["launch_job_background"].assert_called_once_with(20, allow_retry=False)
    assert launch_observed == ["commit"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "blocked",
    [
        "csrf",
        "view",
        "analyze",
        "tenant",
        "missing",
        "foreign-source",
        "actor",
        "window",
        "empty",
        "pending",
        "running",
    ],
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
    elif blocked == "actor":
        scope["get_current_user"] = lambda: NS(id=82)
    elif blocked == "window":
        row.summary_data = {}
    elif blocked == "empty":
        collected.for_source.return_value = []
    else:
        scope["_source_has_running_job"].return_value = {
            "collect": blocked == "pending",
            "analyze": blocked == "running",
        }
    await scope["analytics_retry"](
        NS(state=NS(web_user=NS(id=81))), 1127, token="fake", tenant_id=7
    )
    collected.reset_attempts.assert_not_awaited()
    jobs.enqueue.assert_not_awaited()
    assert tx.outcome is None


@pytest.mark.asyncio
async def test_enqueue_failure_rolls_back_reset_and_never_reports_success():
    scope, _, _, _, jobs, session, tx = route_scope()
    jobs.enqueue.side_effect = RuntimeError("synthetic queue failure")
    with pytest.raises(RuntimeError):
        await scope["analytics_retry"](
            NS(state=NS(web_user=NS(id=81))), 1127, token="fake", tenant_id=7
        )
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
        retry={
            "needed": True,
            "can_retry": True,
            "available": True,
            "window_known": True,
            "status": None,
            "poll_url": None,
            "progress": None,
            "blocking": [],
            "saved_analyses": [],
            "task_links": [],
            "scenario_pinned": True,
        },
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


@pytest.mark.parametrize("is_superuser", [False, True])
def test_retry_template_reuses_post_csrf_confirmation_and_existing_button_style(is_superuser):
    partial = (ROOT / "app/web/templates/web/analytics_retry_status.html").read_text()
    env = Environment(
        loader=DictLoader(
            {
                "web/_run_progress.html": PROGRESS_MACRO,
                "web/analytics_retry_status.html": partial,
            }
        ),
        autoescape=select_autoescape(("html",)),
    )
    context = template_context(is_superuser=is_superuser)
    html = env.get_template("web/analytics_retry_status.html").render(
        retry=context["retry"], analysis=context["analysis"], csrf=context["csrf"], is_superuser=is_superuser
    )
    assert 'action="/app/analytics/1127/retry"' in html
    assert 'method="post"' in html and 'name="_csrf" value="fixture-only"' in html
    assert ('name="tenant_id" value="7"' in html) is is_superuser
    assert 'data-confirm-title="Повторить анализ?"' in html
    assert "bg-violet-500" in html
    assert "Период исходных материалов" not in html


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"can_retry": False}, None),
        ({"needed": False}, None),
        ({"status": "running"}, None),
        ({"available": False}, "нет оставшихся сырых материалов"),
        ({"available": False, "window_known": False}, "Период анализа не сохранён"),
    ],
)
def test_retry_template_only_offers_action_when_appropriate(overrides, message):
    partial = (ROOT / "app/web/templates/web/analytics_retry_status.html").read_text()
    env = Environment(
        loader=DictLoader(
            {
                "web/_run_progress.html": PROGRESS_MACRO,
                "web/analytics_retry_status.html": partial,
            }
        ),
        autoescape=select_autoescape(("html",)),
    )
    context = template_context()
    context["retry"].update(overrides)
    html = env.get_template("web/analytics_retry_status.html").render(
        retry=context["retry"], analysis=context["analysis"], csrf=context["csrf"], is_superuser=False
    )
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


def source_html(rights=True, can_view_tasks=None, can_create_tasks=None, **overrides):
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
    can_view_tasks = rights if can_view_tasks is None else can_view_tasks
    can_create_tasks = rights if can_create_tasks is None else can_create_tasks
    context = dict(
        source=source,
        perms=NS(
            can=lambda model, action: (
                can_view_tasks if (model, action) == ("agenttask", "view")
                else can_create_tasks if (model, action) == ("agenttask", "create")
                else rights
            )
        ),
        csrf="fake",
        is_superuser=False,
        can_update=rights,
        can_view_tasks=can_view_tasks,
        analytics_count=1,
        recent_count=1,
        mode=NS(key="pull", label="API платформы", hint="Fixture", is_push=False),
        modes=(("pull", "API"), ("push", "Bot"), ("user", "User")),
        mode_labels={"pull": "API платформы", "push": "Бот-API", "user": "Личный аккаунт"},
        members=[NS(id=4, username="member")],
        token_owner=NS(id=4, username="member"),
        orphaned_owner_id=None,
        owner_label="member (личный токен)",
        owner_connection=None,
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
        request=NS(url=NS(path="/app/sources/2939", query="")),
    )
    context.update(overrides)
    return env.get_template("web/source_detail.html").render(**context)


def test_source_detail_shows_entity_settings_stats_and_one_task_cta():
    task = NS(
        id=51,
        name="Daily scan",
        effective_active=True,
        is_active=True,
        cron_label="Ежедневно",
        next_run_label="завтра",
    )
    html = source_html(schedules=[task])
    assert "Настройки источника" in html
    assert html.count('href="/app/tasks?create=1&amp;source_id=2939"') == 1
    assert 'name="mode"' in html
    assert 'name="token_owner"' in html
    assert 'name="is_active"' in html
    assert "Связанные с источником задачи" in html
    assert 'href="/app/tasks/51"' in html
    assert html.count("Создать задачу для источника") == 1
    assert "progress" not in html.lower()
    assert "Повторить анализ" not in html
    assert "Последние анализы" not in html


def test_source_detail_permission_gates_settings_task_list_and_cta():
    html = source_html(rights=False, can_view_tasks=False, can_create_tasks=False)
    assert "У вас нет права изменять настройки источника." in html
    assert 'method="post" action="/app/sources/2939"' not in html
    assert "Нет доступа к просмотру задач." in html
    assert "Создать задачу для источника" not in html


def test_source_create_cta_does_not_require_task_view_permission():
    html = source_html(rights=False, can_view_tasks=False, can_create_tasks=True)
    assert "Нет доступа к просмотру задач." in html
    assert html.count("Создать задачу для источника") == 1
    assert html.count('href="/app/tasks?create=1&amp;source_id=2939"') == 1


def source_edit_scope(form_data):
    scope = functions("app/web/sources.py", "source_edit", "_validate_token_owner", "_clean_params")
    source = NS(
        id=2939,
        tenant_id=7,
        name="Fixture",
        platform=NS(id=1),
        platform_id=1,
        external_id="123",
        source_type=NS(db_value="user"),
        is_active=True,
        params={"mode": "pull", "token_owner": 4, "runtime_watermark": "keep"},
    )
    manager = NS(get=AsyncMock(return_value=source), update_by_id=AsyncMock())
    flashes = Mock()
    request = NS(form=AsyncMock(return_value=form_data))
    scope.update(
        Source=NS(objects=manager),
        SourceType=NS(get_by_value=lambda _value: source.source_type),
        ensure_csrf=Mock(return_value=True),
        action_tenant_id=Mock(return_value=7),
        guard_web=Mock(return_value=None),
        add_flash=flashes,
        RedirectResponse=Response,
        tenant_scope=lambda _tenant_id: nullcontext(),
        COLLECTION_MODES=(
            ("pull", "API"),
            ("push", "Bot"),
            ("user", "User"),
        ),
        _workspace_members=AsyncMock(return_value=[NS(id=4), NS(id=8)]),
    )
    return scope, request, source, manager, flashes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("form_data", "token_owner", "expected_owner", "should_update"),
    [
        ({"mode": "pull"}, "", 4, True),
        ({"token_owner": "", "mode": "pull"}, "", None, True),
        ({"token_owner": "8", "mode": "pull"}, "8", 8, True),
        ({"token_owner": "99", "mode": "pull"}, "99", 4, False),
    ],
)
async def test_source_edit_token_owner_respects_posted_field_membership(
    form_data, token_owner, expected_owner, should_update
):
    scope, request, _source, manager, flashes = source_edit_scope(form_data)
    response = await scope["source_edit"](
        request,
        2939,
        name="Fixture",
        platform="",
        external_id="123",
        source_type="",
        is_active="on",
        monitored_users="",
        mode="pull",
        token_owner=token_owner,
        token="csrf",
        tenant_id=7,
    )

    if should_update:
        assert response.url == "/app/sources/2939"
        saved_params = manager.update_by_id.await_args.kwargs["params"]
        assert saved_params.get("token_owner") == expected_owner
        assert saved_params["runtime_watermark"] == "keep"
    else:
        assert response.url == "/app/sources"
        manager.update_by_id.assert_not_awaited()
        flashes.assert_called_once()
    request.form.assert_awaited_once()


@pytest.mark.asyncio
async def test_source_raw_content_delete_requires_admin_csrf_and_idle_source():
    scope = functions("app/web/sources.py", "source_delete_unprocessed")
    source = NS(id=2939, tenant_id=7)
    manager = NS(get=AsyncMock(return_value=source))
    staged = NS(delete_unprocessed_for_source=AsyncMock(return_value=3))
    session = NS(begin=lambda: Transaction(), close=AsyncMock())
    job_manager = NS(lock_source_admission=AsyncMock())
    scope.update(
        ensure_csrf=Mock(return_value=True),
        _diagnostics_admin=Mock(return_value=True),
        action_tenant_id=Mock(return_value=7),
        add_flash=Mock(),
        RedirectResponse=Response,
        Source=NS(objects=manager),
        CollectedItem=NS(objects=staged),
        new_session=Mock(return_value=session),
        tenant_scope=lambda tenant_id: nullcontext(),
        JobManager=job_manager,
        _source_has_running_job=AsyncMock(return_value={"collect": False, "analyze": False}),
    )

    response = await scope["source_delete_unprocessed"](NS(), 2939, token="fake", tenant_id=7)
    assert response.url == "/app/sources/2939"
    job_manager.lock_source_admission.assert_awaited_once_with(session, 7)
    manager.get.assert_awaited_once_with(id=2939, tenant_id=7, session=session)
    staged.delete_unprocessed_for_source.assert_awaited_once_with(session, 2939, 7)
    assert scope["add_flash"].call_args.args[1] == "success"
    session.close.assert_awaited_once()

    for blocked in ("admin", "csrf", "busy"):
        blocked_scope = functions("app/web/sources.py", "source_delete_unprocessed")
        blocked_scope.update(scope)
        blocked_scope["new_session"].reset_mock()
        staged.delete_unprocessed_for_source.reset_mock()
        blocked_scope["_diagnostics_admin"].return_value = True
        blocked_scope["ensure_csrf"].return_value = True
        blocked_scope["_source_has_running_job"].return_value = {"collect": False, "analyze": False}
        if blocked == "admin":
            blocked_scope["_diagnostics_admin"].return_value = False
        elif blocked == "csrf":
            blocked_scope["ensure_csrf"].return_value = False
        else:
            blocked_scope["_source_has_running_job"].return_value = {"collect": True, "analyze": False}
        await blocked_scope["source_delete_unprocessed"](NS(), 2939, token="fake", tenant_id=7)
        if blocked in ("admin", "csrf"):
            assert blocked_scope["new_session"].call_count == 0
        else:
            staged.delete_unprocessed_for_source.assert_not_awaited()


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
        await scope[function](NS(state=NS(web_user=NS(id=81))), 1127, token="fake", tenant_id=7)
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
