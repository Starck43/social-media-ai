"""No-DB checks for the analysis retry status projection and partial."""

from __future__ import annotations

import ast
import re
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import unquote, urlsplit

import pytest
from jinja2 import DictLoader, Environment, select_autoescape

ROOT = Path(__file__).resolve().parents[2]


def functions(*names):
    tree = ast.parse((ROOT / "app/web/analytics.py").read_text())

    class NoAppImports(ast.NodeTransformer):
        def visit_ImportFrom(self, node):
            return None if node.module and node.module.startswith("app.") else node

    nodes = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            nodes.append(NoAppImports().visit(node))
    assert len(nodes) == len(names)
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes],
        type_ignores=[],
    )
    scope = {"Form": lambda default=None, **_kwargs: default}
    exec(compile(ast.fix_missing_locations(module), "app/web/analytics.py", "exec"), scope)
    return scope


def test_task_detail_is_a_safe_back_target_but_nested_task_routes_are_rejected():
    scope = functions("_safe_analytics_return", "_analysis_back_label")
    scope.update(re=re, unquote=unquote, urlsplit=urlsplit)
    assert scope["_safe_analytics_return"]("/app/tasks/51") == "/app/tasks/51"
    assert scope["_analysis_back_label"]("/app/tasks/51") == "К задаче"
    assert scope["_safe_analytics_return"]("/app/tasks/51/edit") is None
    assert scope["_safe_analytics_return"]("//evil.example/app/tasks/51") is None


def retry_scope(job, *, other_jobs=()):
    from app.services.ai.analysis_retry import parse_staged_window

    class Query:
        def __init__(self, value=None):
            self.value = value
            self.filters = None

        def filter(self, **kwargs):
            self.filters = kwargs
            return self

        async def first(self):
            return self.value

    job_query = Query(job)
    count_for_source = AsyncMock(return_value=4)
    source_jobs = AsyncMock(return_value=list(other_jobs) + [job])
    job_manager = NS(source_progress_jobs=source_jobs, pending_analysis_retry_matches=Mock(return_value=False))
    task_manager = NS(for_source=AsyncMock(return_value=[]))
    collected = NS(count_for_source=count_for_source)
    scope = functions("_retry_job_state", "_analysis_retry_context")
    scope.update(
        AIAnalytics=NS(objects=NS()),
        AgentScenario=NS(objects=NS(filter=lambda **_kwargs: Query(None))),
        AgentTaskManager=lambda: task_manager,
        CollectedItem=NS(objects=collected),
        Job=NS(objects=NS(filter=lambda **kwargs: _capture_query(job_query, kwargs))),
        JobManager=lambda: job_manager,
        tenant_scope=lambda _tenant_id: nullcontext(),
        parse_staged_window=parse_staged_window,
        project_job=lambda current, _source_id: {
            "job_id": current.id,
            "status": "pending" if current.status == "pending" else "running",
            "label": "В очереди",
            "percent": 20,
            "processed": 1,
            "total": 5,
        },
        perms_can=lambda *_args: False,
        _safe_analytics_return=lambda value: (
            value if isinstance(value, str) and value.startswith("/app/") and not value.startswith("//") else None
        ),
        _analytics_url=lambda _request, path, **kwargs: path + "?return_to=" + kwargs.get("return_to", ""),
        render_analysis=lambda *_args, **_kwargs: {"display_title": "saved"},
    )
    display = {
        "coverage": {"incomplete": True},
        "analysis_summary": "summary",
        "metrics_legacy": False,
        "content_window_start": "2026-09-10T12:30:00+00:00",
        "content_window_end": "2026-09-10T19:45:00+00:00",
        "scenario_metadata": {},
    }
    row = NS(id=1127, tenant_id=7, source_id=2939, source=NS(tenant_id=7, is_active=True, name="Source"))
    return scope, row, display, job_query, count_for_source, source_jobs, task_manager, job_manager


def _capture_query(query, kwargs):
    query.filters = kwargs
    return query


@pytest.mark.asyncio
async def test_retry_context_accepts_only_exact_task_tenant_source_and_window():
    from app.services.ai.analysis_retry import parse_staged_window

    window = {key: value.isoformat() for key, value in parse_staged_window(
        {"start_date": "2026-09-10T12:30:00+00:00", "end_date": "2026-09-10T19:45:00+00:00"}
    ).items()}
    job = NS(
        id=20,
        tenant_id=7,
        job_type="analyze",
        status="pending",
        agent_task_id=None,
        payload={"retry_analytics_id": 1127, "source_ids": [2939], "staged_window": window},
        result={},
    )
    scope, row, display, query, count, source_jobs, _tasks, _manager = retry_scope(job)
    scope["perms_can"] = lambda _request, model, action: (model, action) == ("source", "analyze")

    context = await scope["_analysis_retry_context"](NS(), row, display, retry_job_id=20)

    assert query.filters == {"id": 20, "tenant_id": 7}
    assert context["job_id"] == 20
    assert context["status"] == "pending"
    assert context["poll_url"] == "/app/analytics/1127/retry-status?retry_job_id=20"
    assert context["available"] is True
    count.assert_awaited_once_with(2939, **parse_staged_window(window))
    source_jobs.assert_awaited_once_with(2939, limit=20)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload_change",
    [
        {"retry_analytics_id": 999},
        {"source_ids": [777]},
        {"staged_window": {"start_date": "2026-09-10T12:30:00+00:00", "end_date": "2026-09-10T19:46:00+00:00"}},
        {"_tenant_id": 8},
        {"_job_type": "collect"},
        {"_id": 21},
    ],
)
async def test_retry_context_rejects_mismatched_tracked_job_before_backlog_read(payload_change):
    from app.services.ai.analysis_retry import parse_staged_window

    original_window = {key: value.isoformat() for key, value in parse_staged_window(
        {"start_date": "2026-09-10T12:30:00+00:00", "end_date": "2026-09-10T19:45:00+00:00"}
    ).items()}
    payload = {"retry_analytics_id": 1127, "source_ids": [2939], "staged_window": original_window}
    payload.update({key: value for key, value in payload_change.items() if not key.startswith("_")})
    job = NS(
        id=20,
        tenant_id=7,
        job_type="analyze",
        status="pending",
        agent_task_id=None,
        payload=payload,
        result={},
    )
    if "_tenant_id" in payload_change:
        job.tenant_id = payload_change["_tenant_id"]
    if "_job_type" in payload_change:
        job.job_type = payload_change["_job_type"]
    if "_id" in payload_change:
        job.id = payload_change["_id"]
    scope, row, display, _query, count, source_jobs, _tasks, _manager = retry_scope(job)

    with pytest.raises(LookupError):
        await scope["_analysis_retry_context"](NS(), row, display, retry_job_id=20)

    count.assert_not_awaited()
    source_jobs.assert_not_awaited()


@pytest.mark.asyncio
async def test_saved_retry_results_match_identity_only_by_source_date_and_window():
    from app.services.ai.analysis_retry import parse_staged_window

    window = {key: value.isoformat() for key, value in parse_staged_window(
        {"start_date": "2026-09-10T12:30:00+00:00", "end_date": "2026-09-10T19:45:00+00:00"}
    ).items()}
    date_range = {"start": window["start_date"], "end": window["end_date"]}
    job = NS(
        id=20,
        tenant_id=7,
        job_type="analyze",
        status="done",
        agent_task_id=None,
        payload={"retry_analytics_id": 1127, "source_ids": [2939], "staged_window": window},
        result={"per_source": [{"source_id": 2939, "analytics_ids": [1140, 1141]}, {"source_id": 1, "analytics_ids": [999]}]},
    )
    first = NS(
        id=1140,
        tenant_id=7,
        source_id=2939,
        analysis_date=date(2026, 9, 10),
        period_type="day",
        summary_data=date_range,
    )
    second = NS(
        id=1141,
        tenant_id=7,
        source_id=2939,
        analysis_date=date(2026, 9, 10),
        period_type="week",
        summary_data=date_range,
    )
    scope, row, display, _query, _count, _source_jobs, _tasks, _manager = retry_scope(job)
    row.analysis_date = date(2026, 9, 10)
    row.period_type = "day"
    scope["AIAnalytics"] = NS(objects=NS(filter=AsyncMock(return_value=[first, second])))
    scope["render_analysis"] = lambda data, **_kwargs: {
        "display_title": "Persisted result",
        "content_window_start": data.get("start"),
        "content_window_end": data.get("end"),
    }
    context = await scope["_analysis_retry_context"](
        NS(), row, display, retry_job_id=20, origin_url="/app/analytics/1127"
    )
    assert context["saved_analyses"][0]["same_identity"] is True
    assert context["saved_analyses"][1]["same_identity"] is False
    assert context["saved_analyses"][0]["id"] == 1140


@pytest.mark.parametrize(
    "job_status,projected_status,expected",
    [
        ("pending", "pending", "pending"),
        ("running", "pending", "running"),
        ("running", "unconfirmed", "running"),
        ("done", "partial", "partial"),
        ("done", "empty", "empty"),
        ("done", "skipped", "skipped"),
        ("failed", "failed", "failed"),
    ],
)
def test_retry_job_state_distinguishes_queue_live_heartbeat_and_unconfirmed(
    job_status, projected_status, expected
):
    scope = functions("_retry_job_state")
    scope["project_job"] = lambda *_args: {
        "job_id": 20,
        "status": projected_status,
        "label": "legacy",
        "percent": None,
        "processed": None,
        "total": None,
    }
    state, progress = scope["_retry_job_state"](
        NS(id=20, status=job_status, locked_at=datetime.now(timezone.utc)), 2939
    )
    assert state == expected
    assert progress["status"] == expected
    if job_status == "pending":
        assert progress["note"] == "Выполнение ещё не началось."


def test_aged_pending_job_is_not_presented_as_running():
    scope = functions("_retry_job_state")
    scope["project_job"] = lambda *_args: {
        "status": "pending",
        "label": "В очереди",
        "percent": None,
        "processed": None,
        "total": None,
    }
    old_job = NS(
        id=20,
        status="pending",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=3),
    )
    state, progress = scope["_retry_job_state"](old_job, 2939)
    assert state == "pending"
    assert progress["note"] == "Запуск ожидает обработчика; активное выполнение не подтверждено."


def test_running_state_uses_job_heartbeat_not_source_projection():
    scope = functions("_retry_job_state")
    scope["project_job"] = lambda *_args: {
        "status": "running",
        "label": "Выполняется",
        "percent": None,
        "processed": None,
        "total": None,
    }
    stale_job = NS(id=20, status="running", locked_at=datetime.now(timezone.utc) - timedelta(minutes=3))
    state, progress = scope["_retry_job_state"](stale_job, 2939)
    assert state == progress["status"] == "unconfirmed"


@pytest.mark.asyncio
async def test_complete_summary_with_legacy_metrics_does_not_query_retry_backlog_or_activity():
    job = NS(id=20, tenant_id=7, job_type="analyze", status="done", payload={}, result={})
    scope, row, display, _query, count, source_jobs, task_manager, _manager = retry_scope(job)
    display.update(coverage={"incomplete": False}, analysis_summary="complete", metrics_legacy=True)
    task_manager.for_source = AsyncMock(return_value=[NS(id=51, name="Daily analysis")])
    scope["perms_can"] = lambda _request, model, action: (model, action) == ("agenttask", "view")
    context = await scope["_analysis_retry_context"](NS(), row, display)
    assert context["needed"] is False
    assert context["available"] is False
    count.assert_not_awaited()
    source_jobs.assert_not_awaited()
    task_manager.for_source.assert_awaited_once_with(2939, 7, source_is_active=True)
    assert context["task_links"][0]["id"] == 51


def render_status(retry):
    macro = (
        "{% macro progress_bar(label, state='unknown', percent=none, processed=none, total=none, note=none) %}"
        "<div data-progress-state='{{ state }}'>{{ label }} {{ note or '' }}</div>{% endmacro %}"
    )
    env = Environment(
        loader=DictLoader(
            {
                "web/_run_progress.html": macro,
                "web/analytics_retry_status.html": (
                    ROOT / "app/web/templates/web/analytics_retry_status.html"
                ).read_text(),
            }
        ),
        autoescape=select_autoescape(("html",)),
    )
    return env.get_template("web/analytics_retry_status.html").render(
        retry=retry,
        analysis=NS(id=1127, tenant_id=7),
        csrf="token",
        is_superuser=False,
    )


def test_retry_partial_polls_only_active_state_and_shows_task_guidance():
    active = {
        "poll_url": "/app/analytics/1127/retry-status?retry_job_id=20",
        "status": "running",
        "adoptable_job_id": None,
        "progress": {"label": "Выполняется", "percent": 40, "processed": 2, "total": 5},
        "blocking": [],
        "saved_analyses": [],
        "needed": True,
        "can_retry": False,
        "available": False,
        "window_known": True,
        "scenario_pinned": False,
        "task_links": [{"id": 51, "name": "Daily analysis"}],
    }
    html = render_status(active)
    assert 'hx-get="/app/analytics/1127/retry-status?retry_job_id=20"' in html
    assert "Выполняется" in html
    assert "Daily analysis" in html
    assert "Job #" not in html

    terminal = {**active, "poll_url": None, "status": "done", "available": False}
    html = render_status(terminal)
    assert "hx-get=" not in html
    assert "повторный анализ использует" not in html

    watching = {
        **active,
        "status": None,
        "progress": None,
        "poll_url": "/app/analytics/1127/retry-status?watch_active=1",
        "blocking": [{"status": "pending", "progress": {"label": "В очереди"}}],
    }
    html = render_status(watching)
    assert 'hx-get="/app/analytics/1127/retry-status?watch_active=1"' in html
    assert "В очереди" in html


def test_retry_partial_offers_only_explicit_resume_form_for_adoptable_job():
    retry = {
        "poll_url": None,
        "status": "pending",
        "progress": {"percent": None, "processed": None, "total": None},
        "blocking": [],
        "saved_analyses": [],
        "needed": True,
        "can_retry": True,
        "available": False,
        "window_known": True,
        "scenario_pinned": True,
        "adoptable_job_id": 1763,
        "task_links": [],
    }
    html = render_status(retry)
    assert 'action="/app/analytics/1127/retry/1763/resume"' in html
    assert 'name="_csrf" value="token"' in html
    assert "Запустить ожидающий анализ" in html
    retry["blocking"] = [{"job_id": 8, "progress": {"label": "В очереди"}}]
    html = render_status(retry)
    assert 'action="/app/analytics/1127/retry/1763/resume"' not in html
    assert "Предыдущий запуск по задаче анализа" in html
    assert "В очереди" in html
    assert "Job #" not in html


def test_pending_retry_adoption_predicate_is_exact_and_one_shot():
    from app.models.managers.job_manager import JobManager
    from app.services.ai.analysis_retry import parse_staged_window

    window = parse_staged_window(
        {"start_date": "2026-09-10T12:30:00+00:00", "end_date": "2026-09-10T19:45:00+00:00"}
    )
    now = parse_staged_window(
        {"start_date": "2026-10-20T12:30:00+00:00", "end_date": "2026-10-20T19:45:00+00:00"}
    )["end_date"]
    job = NS(
        tenant_id=7,
        job_type="analyze",
        agent_task_id=None,
        status="pending",
        run_at=now,
        attempts=0,
        initiated_by_user_id=None,
        payload={
            "source_ids": [2939],
            "staged_window": {key: value.isoformat() for key, value in window.items()},
        },
    )
    args = dict(
        tenant_id=7,
        analysis_id=1127,
        source_id=2939,
        staged_window=window,
        initiated_by_user_id=81,
        now=now,
    )
    assert JobManager.pending_analysis_retry_matches(job, **args)
    variants = [
        {"tenant_id": 8},
        {"job_type": "collect"},
        {"agent_task_id": 5},
        {"status": "running"},
        {"run_at": now.replace(year=2026, month=10, day=21)},
        {"attempts": 1},
        {"initiated_by_user_id": 82},
        {"payload": {**job.payload, "source_ids": [1]}},
        {"payload": {**job.payload, "retry_analytics_id": 999}},
        {"payload": {**job.payload, "source_ids": [True]}},
    ]
    for changes in variants:
        candidate = NS(**{**vars(job), **changes})
        assert not JobManager.pending_analysis_retry_matches(candidate, **args)
    bool_source = NS(**vars(job))
    bool_source.payload = {**job.payload, "source_ids": [True]}
    assert not JobManager.pending_analysis_retry_matches(bool_source, **{**args, "source_id": 1})


@pytest.mark.asyncio
async def test_actual_adopt_manager_stamps_exact_job_under_mocked_transaction():
    from app.models.managers.job_manager import JobManager
    from app.services.ai.analysis_retry import parse_staged_window

    window = parse_staged_window(
        {"start_date": "2026-09-10T12:30:00+00:00", "end_date": "2026-09-10T19:45:00+00:00"}
    )
    now = datetime.now(timezone.utc)
    job = NS(
        id=1763,
        tenant_id=7,
        job_type="analyze",
        agent_task_id=None,
        status="pending",
        run_at=now,
        attempts=0,
        initiated_by_user_id=None,
        payload={
            "source_ids": [2939],
            "staged_window": {key: value.isoformat() for key, value in window.items()},
        },
    )

    class Transaction:
        outcome = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, kind, _error, _traceback):
            self.outcome = "rollback" if kind else "commit"

    class Session:
        def __init__(self):
            self.transaction = Transaction()
            self.execute = AsyncMock(return_value=NS(scalar_one_or_none=lambda: job))
            self.flush = AsyncMock()

        def begin(self):
            return self.transaction

    class SessionFactory:
        def __init__(self, session):
            self.session = session

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, *_args):
            return None

    session = Session()
    manager = JobManager()
    manager._source_activity_context = AsyncMock(return_value=([job], {}, set()))
    admission_lock = AsyncMock()
    with (
        patch("app.core.database.async_session_maker", return_value=SessionFactory(session)),
        patch("app.core.permissions.get_current_user", return_value=NS(id=81)),
        patch("app.core.tenant_context.current_tenant_id", return_value=7),
        patch("app.core.tenant_context.is_bypass", return_value=False),
        patch.object(JobManager, "lock_source_admission", new=admission_lock),
    ):
        arguments = dict(
            job_id=1763,
            tenant_id=7,
            analysis_id=1127,
            source_id=2939,
            staged_window=window,
            initiated_by_user_id=81,
        )
        assert await manager.adopt_pending_analysis_retry(**arguments)
        # Repeated explicit POST after a lost launch acknowledgment remains safe.
        assert await manager.adopt_pending_analysis_retry(**arguments)
    assert session.transaction.outcome == "commit"
    assert job.initiated_by_user_id == 81
    assert job.payload["retry_analytics_id"] == 1127
    assert "retry_resume_requested_at" not in job.payload
    assert admission_lock.await_count == 2


@pytest.mark.asyncio
async def test_actual_adopt_manager_rolls_back_if_stamp_flush_fails():
    from app.models.managers.job_manager import JobManager
    from app.services.ai.analysis_retry import parse_staged_window

    window = parse_staged_window(
        {"start_date": "2026-09-10T12:30:00+00:00", "end_date": "2026-09-10T19:45:00+00:00"}
    )
    job = NS(
        id=1763,
        tenant_id=7,
        job_type="analyze",
        agent_task_id=None,
        status="pending",
        run_at=datetime.now(timezone.utc),
        attempts=0,
        initiated_by_user_id=None,
        payload={"source_ids": [2939], "staged_window": {key: value.isoformat() for key, value in window.items()}},
    )

    class Transaction:
        outcome = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, kind, _error, _traceback):
            self.outcome = "rollback" if kind else "commit"

    class Session:
        def __init__(self):
            self.transaction = Transaction()
            self.execute = AsyncMock(return_value=NS(scalar_one_or_none=lambda: job))
            self.flush = AsyncMock(side_effect=RuntimeError("synthetic flush failure"))

        def begin(self):
            return self.transaction

    class SessionFactory:
        def __init__(self, session):
            self.session = session

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, *_args):
            return None

    session = Session()
    manager = JobManager()
    manager._source_activity_context = AsyncMock(return_value=([job], {}, set()))
    with (
        patch("app.core.database.async_session_maker", return_value=SessionFactory(session)),
        patch("app.core.permissions.get_current_user", return_value=NS(id=81)),
        patch("app.core.tenant_context.current_tenant_id", return_value=7),
        patch("app.core.tenant_context.is_bypass", return_value=False),
        patch.object(JobManager, "lock_source_admission", new=AsyncMock()),
    ):
        with pytest.raises(RuntimeError, match="synthetic flush failure"):
            await manager.adopt_pending_analysis_retry(
                job_id=1763,
                tenant_id=7,
                analysis_id=1127,
                source_id=2939,
                staged_window=window,
                initiated_by_user_id=81,
            )
    assert session.transaction.outcome == "rollback"


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", ["csrf", "actor", "manager", "ok"])
async def test_resume_route_only_launches_after_authenticated_manager_ack(denied):
    scope = functions("analytics_resume_pending_retry")

    class Query:
        def select_related(self, *_args):
            return self

        def filter(self, **_kwargs):
            return self

        async def first(self):
            return row

    row = NS(id=1127, tenant_id=7, source_id=2939, source=NS(tenant_id=7), summary_data={})
    manager = NS(adopt_pending_analysis_retry=AsyncMock(return_value=denied != "manager"))
    launched = []
    flashes = []
    scope.update(
        ensure_csrf=Mock(return_value=denied != "csrf"),
        guard_web=Mock(return_value=None),
        action_tenant_id=Mock(return_value=7),
        render=lambda *_args, **kwargs: NS(status_code=kwargs.get("status_code", 200)),
        RedirectResponse=lambda url, status_code=302: NS(url=url, status_code=status_code),
        add_flash=lambda *_args: flashes.append(_args),
        tenant_scope=lambda _tenant_id: nullcontext(),
        AIAnalytics=NS(objects=NS(select_related=lambda *_args: Query())),
        render_analysis=lambda *_args: {
            "content_window_start": "2026-09-10T12:30:00+00:00",
            "content_window_end": "2026-09-10T19:45:00+00:00",
        },
        parse_staged_window=lambda value: value,
        JobManager=lambda: manager,
        get_current_user=lambda: NS(id=81),
        current_tenant_id=lambda: 7,
        is_bypass=lambda: False,
        launch_job_background=lambda job_id, **kwargs: launched.append((job_id, kwargs, manager.adopt_pending_analysis_retry.await_count)),
    )
    actor_id = 82 if denied == "actor" else 81
    request = NS(state=NS(web_user=NS(id=actor_id)))
    response = await scope["analytics_resume_pending_retry"](
        request, 1127, 1763, token="csrf", tenant_id=7
    )
    if denied in ("csrf", "actor", "manager"):
        assert not launched
        if denied == "manager":
            manager.adopt_pending_analysis_retry.assert_awaited_once()
        else:
            manager.adopt_pending_analysis_retry.assert_not_awaited()
        return
    assert launched == [(1763, {"allow_retry": False}, 1)]
    assert response.url == "/app/analytics/1127?retry_job_id=1763"


@pytest.mark.asyncio
async def test_retry_status_requires_analytics_view_and_refreshes_only_terminal_job():
    scope = functions("analytics_retry_status")

    class HtmlResponse:
        def __init__(self, content, status_code):
            self.content = content
            self.status_code = status_code

    class RowQuery:
        def __init__(self, row):
            self.row = row
            self.filters = None

        def select_related(self, *_args):
            return self

        def filter(self, **kwargs):
            self.filters = kwargs
            return self

        async def first(self):
            return self.row

    row = NS(id=1127, tenant_id=7, source=NS(tenant_id=7, name="Source"), summary_data={})
    row_query = RowQuery(row)
    analytics = NS(objects=NS(select_related=lambda *_args: row_query))
    scope.update(
        HTMLResponse=HtmlResponse,
        AIAnalytics=analytics,
        tenant_scope=lambda _tenant_id: nullcontext(),
        render_analysis=lambda *_args, **_kwargs: {"title": "analysis"},
        _safe_analytics_return=lambda value: value if isinstance(value, str) and value.startswith("/app/analytics/") else None,
        _analysis_retry_context=AsyncMock(return_value={"status": "done"}),
        render=lambda *_args, **_kwargs: NS(headers={}),
    )

    scope["perms_can"] = lambda *_args: False
    denied = await scope["analytics_retry_status"](NS(state=NS(tenant_id=7)), 1127, 20)
    assert denied.status_code == 403
    assert row_query.filters is None

    scope["perms_can"] = lambda _request, resource, action: (resource, action) == ("aianalytics", "view")
    request = NS(state=NS(tenant_id=7))
    response = await scope["analytics_retry_status"](request, 1127, 20)
    assert row_query.filters == {"id": 1127, "tenant_id": 7}
    assert response.headers["HX-Refresh"] == "true"

    scope["_analysis_retry_context"].return_value = {"status": "running"}
    response = await scope["analytics_retry_status"](request, 1127, 20)
    assert "HX-Refresh" not in response.headers

    scope["_analysis_retry_context"].return_value = {
        "status": None,
        "poll_url": "/app/analytics/1127/retry-status?watch_active=1",
    }
    response = await scope["analytics_retry_status"](request, 1127)
    assert "HX-Refresh" not in response.headers
    assert scope["_analysis_retry_context"].await_args.kwargs["retry_job_id"] is None

    scope["_analysis_retry_context"].return_value = {"status": None, "poll_url": None}
    response = await scope["analytics_retry_status"](request, 1127)
    assert response.headers["HX-Refresh"] == "true"

    result_url = "/app/analytics/1140?return_to=%2Fapp%2Fanalytics%2F1127"
    scope["_analysis_retry_context"].return_value = {
        "status": "done",
        "poll_url": None,
        "saved_analyses": [{"id": 1140, "same_identity": True, "url": result_url}],
    }
    response = await scope["analytics_retry_status"](request, 1127, 20)
    assert response.headers["HX-Redirect"] == result_url
    assert "HX-Refresh" not in response.headers

    scope["_analysis_retry_context"].return_value["saved_analyses"] = [
        {"id": 1140, "same_identity": True, "url": "https://evil.example/result"}
    ]
    response = await scope["analytics_retry_status"](request, 1127, 20)
    assert response.headers["HX-Refresh"] == "true"
