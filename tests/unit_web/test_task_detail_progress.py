"""Task detail run panel stays scoped to its task and role-filtered."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.models.managers.agent_task_manager import (
    _busy_admission_receipt,
    _RetryAdmissionBusy,
    _RetryAdmissionTransaction,
)
from app.web import tasks

ROOT = Path(__file__).resolve().parents[2]


class _Query:
    def __init__(self, value):
        self.value = value
        self.criteria = ()
        self.kwargs = {}

    def filter(self, *criteria, **kwargs):
        self.criteria = criteria
        self.kwargs = kwargs
        return self

    def order_by(self, *ordering):
        self.ordering = ordering
        return self

    async def first(self):
        return self.value

    def __await__(self):
        async def _resolve():
            return self.value

        return _resolve().__await__()


async def _async_value(value):
    return value


class _FakeTransaction:
    def __init__(self):
        self.exit_exception = None
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, _traceback):
        self.exit_exception = exc
        self.rolled_back = exc_type is not None
        return False


class _FakeSession:
    def __init__(self):
        self.transaction = _FakeTransaction()

    def begin(self):
        return self.transaction


@pytest.mark.asyncio
async def test_retry_busy_rolls_back_before_returning_the_legacy_receipt():
    session = _FakeSession()
    job = SimpleNamespace(id=81)
    transaction = _RetryAdmissionTransaction(session)

    async with transaction:
        raise _RetryAdmissionBusy(job)

    assert session.transaction.rolled_back is True
    assert session.transaction.exit_exception.job is job
    assert transaction.busy_job is job
    assert _busy_admission_receipt(21, job) == {"status": "busy", "task_id": 21, "job": job}


@pytest.mark.asyncio
async def test_latest_progress_endpoint_looks_up_task_and_job_in_same_tenant(monkeypatch):
    task_row = SimpleNamespace(id=21, tenant_id=7, name="Task A", job_type="analyze", payload={})
    job_row = SimpleNamespace(id=31, agent_task_id=21, tenant_id=7, created_at=datetime.now(timezone.utc))
    task_query = _Query(task_row)
    job_query = _Query(job_row)
    monkeypatch.setattr(tasks.AgentTask, "objects", SimpleNamespace(filter=lambda **kw: task_query.filter(**kw)))
    monkeypatch.setattr(tasks.Job, "objects", SimpleNamespace(filter=lambda **kw: job_query.filter(**kw)))
    monkeypatch.setattr(
        tasks, "perms_can", lambda _request, resource, action: (resource, action) == ("agenttask", "view")
    )
    monkeypatch.setattr(
        tasks,
        "_task_run_panel_data",
        lambda *_args, **_kwargs: {"job": job_row, "summary": {}, "sources": [], "can_view_sources": False},
    )
    monkeypatch.setattr(tasks, "_task_run_scope", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(tasks, "render", lambda _request, name, **context: (name, context))
    request = SimpleNamespace(state=SimpleNamespace(tenant_id=7))

    name, context = await tasks.task_latest_progress(request, 21)

    assert name == "web/task_run_progress.html"
    assert task_query.kwargs == {"id": 21, "tenant_id": 7}
    assert job_query.kwargs == {"agent_task_id": 21, "tenant_id": 7, "status__in": ["pending", "running"]}
    assert context["job"] is job_row


@pytest.mark.asyncio
async def test_latest_progress_endpoint_does_not_query_jobs_for_foreign_task(monkeypatch):
    task_query = _Query(None)
    job_queries = []
    monkeypatch.setattr(tasks.AgentTask, "objects", SimpleNamespace(filter=lambda **kw: task_query.filter(**kw)))
    monkeypatch.setattr(tasks.Job, "objects", SimpleNamespace(filter=lambda **kw: job_queries.append(kw)))
    monkeypatch.setattr(tasks, "perms_can", lambda *_args: True)
    request = SimpleNamespace(state=SimpleNamespace(tenant_id=7))

    response = await tasks.task_latest_progress(request, 99)

    assert response.status_code == 404
    assert task_query.kwargs == {"id": 99, "tenant_id": 7}
    assert job_queries == []


@pytest.mark.asyncio
async def test_retry_endpoint_accepts_analyze_task_without_unified_payload_flag(monkeypatch):
    task_row = SimpleNamespace(id=21, tenant_id=7, job_type="analyze", name="Legacy analyze", payload={})

    async def fake_enqueue(task, *, initiated_by_user_id=None, retry_exhausted=False):
        assert task is task_row
        assert retry_exhausted is True
        return SimpleNamespace(id=31)

    monkeypatch.setattr(tasks, "ensure_csrf", lambda *_args: True)
    monkeypatch.setattr(tasks, "guard_web", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tasks.AgentTask, "objects", SimpleNamespace(get=lambda **_kwargs: _async_value(task_row)))
    monkeypatch.setattr(tasks, "add_flash", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.jobs.enqueue.enqueue_task_run", fake_enqueue)
    monkeypatch.setattr("app.jobs.dispatcher.launch_job_background", lambda *_args, **_kwargs: None)
    request = SimpleNamespace(state=SimpleNamespace(tenant_id=7, web_user=SimpleNamespace(id=5)))

    response = await tasks.task_retry_analysis(request, 21, token="valid")

    assert response.status_code == 302
    assert response.headers["location"] == "/app/tasks/21"


@pytest.mark.asyncio
async def test_enqueue_task_run_forwards_retry_and_returns_no_staged_content(monkeypatch):
    from app.jobs.enqueue import enqueue_task_run

    captured = {}

    async def fake_admit(_manager, task_id, **kwargs):
        captured.update(task_id=task_id, **kwargs)
        return {"status": "no_staged_content", "task_id": task_id, "reset_count": 0}

    monkeypatch.setattr(tasks.AgentTaskManager, "_admit_task_run", fake_admit)
    task_row = SimpleNamespace(id=21, tenant_id=7)

    job = await enqueue_task_run(task_row, retry_exhausted=True)

    assert job is None
    assert captured["task_id"] == 21
    assert captured["retry_exhausted"] is True


@pytest.mark.asyncio
async def test_enqueue_task_run_busy_retry_remains_a_conflict(monkeypatch):
    from app.jobs.enqueue import enqueue_task_run
    from app.jobs.source_activity import SourceJobBusy

    existing = SimpleNamespace(id=81, status="running")

    async def fake_admit(_manager, _task_id, **_kwargs):
        return {"status": "busy", "job": existing}

    monkeypatch.setattr(tasks.AgentTaskManager, "_admit_task_run", fake_admit)

    with pytest.raises(SourceJobBusy) as caught:
        await enqueue_task_run(SimpleNamespace(id=21, tenant_id=7), retry_exhausted=True)

    assert caught.value.job is existing


def test_task_run_panel_hides_admin_diagnostics_for_regular_operator(monkeypatch):
    request = SimpleNamespace()
    task = SimpleNamespace(name="Scoped task")
    job = SimpleNamespace(id=44, job_type="analyze", status="done", result={})
    monkeypatch.setattr(tasks, "_job_summary", lambda *_args: {"status": "done", "stats": [{"value": 2}]})
    monkeypatch.setattr(tasks, "_source_links", lambda _job: [{"source_id": 8, "name": "Source"}])
    monkeypatch.setattr(tasks, "_raw_coverage", lambda _job: [{"source_id": 8, "percent": 50}])
    monkeypatch.setattr(tasks, "_diagnostics_view", lambda _job: {"job_id": 44, "error_code": "timeout"})
    monkeypatch.setattr(tasks, "perms_can", lambda _request, resource, action: (resource, action) == ("source", "view"))
    monkeypatch.setattr(tasks, "_diagnostics_admin", lambda _request: False)

    panel = tasks._task_run_panel_data(request, task, job)

    assert panel["summary"]["stats"] == [{"value": 2}]
    assert panel["summary"]["raw_coverage"] == [{"source_id": 8, "percent": 50}]
    assert "diagnostics" not in panel["summary"]
    assert panel["sources"][0]["source_id"] == 8


def test_task_run_panel_keeps_legacy_status_and_scope_when_task_has_no_job(monkeypatch):
    request = SimpleNamespace(state=SimpleNamespace(web_user=None))
    task = SimpleNamespace(name="Scoped task", last_status="queued", last_run_at=datetime.now(timezone.utc))
    monkeypatch.setattr(tasks, "perms_can", lambda *_args: False)

    panel = tasks._task_run_panel_data(request, task, None, run_scope={"total": 4, "active": 3, "platforms": 2})

    assert panel["job"] is None
    assert panel["summary"] is None
    assert panel["legacy_status_label"] == "поставлена в очередь"
    assert panel["run_scope"] == {"total": 4, "active": 3, "platforms": 2}


def test_task_run_panel_includes_allowlisted_diagnostics_only_for_admin(monkeypatch):
    request = SimpleNamespace()
    task = SimpleNamespace(name="Scoped task")
    job = SimpleNamespace(id=44, job_type="analyze", status="failed", result={})
    monkeypatch.setattr(
        tasks, "_job_summary", lambda *_args: {"status": "failed", "error": "secret raw provider exception"}
    )
    monkeypatch.setattr(tasks, "_source_links", lambda _job: [])
    monkeypatch.setattr(tasks, "_raw_coverage", lambda _job: [])
    monkeypatch.setattr(tasks, "_diagnostics_view", lambda _job: {"job_id": 44, "error_code": "timeout"})
    monkeypatch.setattr(tasks, "perms_can", lambda *_args: False)
    monkeypatch.setattr(tasks, "_diagnostics_admin", lambda _request: True)

    panel = tasks._task_run_panel_data(request, task, job)

    assert panel["summary"]["error"] == "Задача завершилась с ошибкой. Повторите запуск позже."
    assert panel["summary"]["diagnostics"] == {"job_id": 44, "error_code": "timeout"}
    assert "secret raw provider exception" not in repr(panel)
    assert "raw_coverage" not in panel["summary"]


def test_task_detail_template_renders_task_run_actions_and_progress_counters():
    # Render the dedicated partial independently; the parent page owns the navbar
    # action form and polling wrapper, while this context is a single task's Job.
    env = Environment(
        loader=FileSystemLoader(ROOT / "app/web/templates"),
        autoescape=select_autoescape(("html",)),
    )
    env.filters["human_dt"] = lambda value, **_kwargs: "сегодня"
    partial = env.get_template("web/task_run_progress.html")
    job = SimpleNamespace(id=31, created_at=datetime(2026, 10, 19, tzinfo=timezone.utc))
    summary = {
        "status": "running",
        "title": "Анализ соцсетей",
        "activity": "Обрабатывает источники",
        "progress": {"phase": "Анализ данных", "completed": 1, "total": 2, "percent": 50, "steps": [], "current": None},
        "stats": [{"value": 3, "label": "новых записей"}],
    }
    output = partial.render(
        task=SimpleNamespace(id=21),
        job=job,
        summary=summary,
        sources=[],
        can_view_sources=True,
        run_progress={"state": "running", "phases": [{"key": "analysis", "label": "Анализ данных", "processed": 3, "total": 10, "percent": 30, "segments": [], "note": "Успешно обработано"}]},
        _tz="UTC",
    )
    assert 'role="progressbar"' in output
    assert "3</b> / 10 записей" in output
    assert "Анализ данных" in output
    assert "из 2 источников" not in output

    page_source = (ROOT / "app/web/templates/web/task_detail.html").read_text()
    assert "return_to_detail" in page_source
    assert "Выполнить сейчас" in page_source
    assert 'action="/app/tasks/{{ task.id }}/run-now"' in page_source
    progress_source = (ROOT / "app/web/templates/web/task_run_progress.html").read_text()
    assert 'hx-get="/app/tasks/{{ task.id }}/latest-progress"' in progress_source
    assert "Повторить анализ" in progress_source
    assert 'action="/app/tasks/{{ task.id }}/retry-analysis"' in progress_source


def test_task_run_partial_shows_pre_run_status_scope_and_retry_only_for_prior_run():
    env = Environment(
        loader=FileSystemLoader(ROOT / "app/web/templates"),
        autoescape=select_autoescape(("html",)),
    )
    env.filters["human_dt"] = lambda value, **_kwargs: "сегодня"
    partial = env.get_template("web/task_run_progress.html")
    output = partial.render(
        task=SimpleNamespace(id=21, job_type="analyze", last_run_at=None, last_status=None, payload={}),
        job=None,
        summary=None,
        sources=[],
        can_view_sources=False,
        run_scope={"total": 4, "active": 3, "platforms": 2},
        legacy_status_label="результат не подтверждён",
        can_retry_analysis=True,
        running_job_id=None,
        csrf="token",
        is_superuser=False,
        filter_tenant_id=None,
        _tz="UTC",
    )
    assert "Ожидает первого запуска" in output
    assert "Источников: 4" not in output
    assert "Источники этого запуска" not in output
    assert "Запуск собирает доступные публикации" not in output
    assert "Повторить анализ" not in output
    assert output.count('id="task-run-progress"') == 1
    assert "Охват задачи" not in output


def test_task_run_partial_keeps_all_run_content_inside_htmx_root():
    env = Environment(
        loader=FileSystemLoader(ROOT / "app/web/templates"),
        autoescape=select_autoescape(("html",)),
    )
    env.filters["human_dt"] = lambda value, **_kwargs: "сегодня"
    partial = env.get_template("web/task_run_progress.html")
    output = partial.render(
        task=SimpleNamespace(id=21, job_type="analyze", last_run_at=None),
        job=SimpleNamespace(id=31, status="done", created_at=datetime.now(timezone.utc)),
        summary={"status": "done", "title": "Анализ", "stats": [{"value": 3, "label": "записей"}]},
        sources=[],
        can_view_sources=True,
        run_scope={
            "total": 4,
            "active": 4,
            "platforms": 2,
            "raw_backlog": {"pending": 10, "exhausted": 3, "eligible": 7, "unknown_sources": 0},
        },
        legacy_status_label="не подтверждён",
        can_retry_analysis=True,
        running_job_id=None,
        csrf="token",
        is_superuser=False,
        filter_tenant_id=None,
        _tz="UTC",
    )
    assert output.index("3") < output.rfind("</div>")
    assert 'role="progressbar"' not in output
    assert 'action="/app/tasks/21/retry-analysis"' in output
    assert "Сырые данные источников задачи" not in output
    assert "Общая очередь выбранных источников за период задачи" not in output
    assert "raw_backlog" not in output


@pytest.mark.asyncio
async def test_task_run_scope_reads_windowed_backlog_only_with_source_view(monkeypatch):
    from app.models.collected_item import CollectedItem
    from app.models.managers.collected_item_manager import CollectedItemManager
    from app.models.source import Source

    sources = [
        SimpleNamespace(
            id=8,
            tenant_id=7,
            is_active=True,
            platform_id=1,
            params={"cli_dates": {"start_date": "2026-01-01"}},
        ),
        SimpleNamespace(
            id=9,
            tenant_id=7,
            is_active=True,
            platform_id=2,
            params={"cli_dates": {"start_date": "bad-date"}},
        ),
    ]
    monkeypatch.setattr(Source, "objects", SimpleNamespace(filter=lambda **_kwargs: _Query(sources)))
    monkeypatch.setattr(tasks, "_task_source_ids", lambda _task_id: _async_value([8, 9]))
    captured = {}

    async def aggregate(windows):
        captured["windows"] = windows
        return {"pending": 5, "exhausted": 2, "eligible": 3}

    def bound_aggregate(manager, windows):
        assert manager.model is CollectedItem
        return aggregate(windows)

    monkeypatch.setattr(CollectedItemManager, "backlog_counts_for_sources", bound_aggregate)
    task = SimpleNamespace(id=21, tenant_id=7, job_type="analyze", payload={})

    denied_scope = await tasks._task_run_scope(task, include_raw=False)
    assert "raw_backlog" not in denied_scope
    assert captured == {}

    visible_scope = await tasks._task_run_scope(task, include_raw=True)
    assert visible_scope["raw_backlog"] == {
        "pending": 5,
        "exhausted": 2,
        "eligible": 3,
        "unknown_sources": 1,
    }
    assert captured["windows"] == [
        {
            "source_id": 8,
            "start_date": tasks.AgentTaskManager.parse_date("2026-01-01"),
            "end_date": None,
        }
    ]


def test_retry_date_window_rejects_malformed_and_reversed_bounds():
    manager = tasks.AgentTaskManager

    with pytest.raises(ValueError):
        manager.resolve_cli_date_window({"start_date": "not-a-date"})
    with pytest.raises(ValueError):
        manager.resolve_cli_date_window({"start_date": "2026-03-02", "end_date": "2026-03-01"})
    assert manager.resolve_cli_date_window({"start_date": "2026-03-01"}) == (
        manager.parse_date("2026-03-01"),
        None,
    )


@pytest.mark.asyncio
async def test_task_llm_diagnostics_are_loaded_only_for_admin_and_exact_task_job(monkeypatch):
    from app.models.managers.llm_attempt_manager import LLMAttemptManager

    captured = []

    async def summary(_self, task_id, job_id):
        captured.append((task_id, job_id))
        return {"model": "model-a", "provider": "provider-a", "succeeded": 2, "failed": 1}

    monkeypatch.setattr(LLMAttemptManager, "task_run_summary", summary)
    monkeypatch.setattr(tasks, "_diagnostics_admin", lambda _request: True)
    request = SimpleNamespace()
    task = SimpleNamespace(id=21)
    job = SimpleNamespace(id=31)
    panel = {"summary": {"diagnostics": {"job_id": 31}}}

    await tasks._attach_task_llm_diagnostics(request, task, job, panel)

    assert captured == [(21, 31)]
    assert panel["summary"]["diagnostics"]["model"] == "model-a"
    assert panel["summary"]["diagnostics"]["succeeded"] == 2


def test_task_detail_collection_summary_renders_the_task_intent():
    """The detail page answers "what does this task collect?" without the editor."""
    env = Environment(
        loader=FileSystemLoader(ROOT / "app/web/templates"),
        autoescape=select_autoescape(("html",)),
    )
    template = env.get_template("web/_task_collection_summary.html")
    payload = {
        "collection": {
            "content_types": ["posts", "comments"],
            "engagement_types": ["likes"],
            "limit": 250,
            "queries": ["доставка"],
            "city": {"name": "Киров", "mode": "prefer"},
            "discovery": {"people": True, "communities": False, "queries": ["дизайн"]},
            "comments": {"max_per_post": 5, "total": 40},
            "candidates": {"limit": 25, "confirm": "auto"},
            "date_window": {"start": "2026-10-01", "end": None},
        }
    }
    output = template.render(
        task=SimpleNamespace(job_type="analyze"),
        collection=tasks._collection_edit(payload),
    )

    assert "Сбор" in output
    assert "записи + комментарии" in output
    assert "лимит 250" in output
    assert "лайки" in output
    assert "Киров" in output and "предпочитать" in output
    assert "доставка" in output
    assert "люди" in output and "дизайн" in output
    assert "до 5 на запись, всего 40" in output
    assert "Кандидаты: 25" in output and "автоматически" in output
    assert "2026-10-01" in output

    assert "Сбор" not in template.render(
        task=SimpleNamespace(job_type="digest"),
        collection=tasks._collection_edit(payload),
    )
