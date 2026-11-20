"""Autonomous regressions for deterministic current-run and live progress state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.web import task_progress
from app.web import task_presenters

__source_only__ = True

ROOT = Path(__file__).resolve().parents[2]


def _job(*, status="running", locked_at=None, attempts=4, result=None):
    return SimpleNamespace(
        id=31,
        status=status,
        locked_at=locked_at,
        attempts=attempts,
        result=result or {},
        job_type="analyze",
        created_at=datetime(2026, 11, 20, tzinfo=timezone.utc),
        error=None,
    )


def test_running_progress_requires_aware_heartbeat_within_90_seconds():
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

    assert task_progress.progress_heartbeat_fresh(
        _job(locked_at=now - timedelta(seconds=90)), now=now
    )
    assert not task_progress.progress_heartbeat_fresh(
        _job(locked_at=now - timedelta(seconds=91)), now=now
    )
    assert not task_progress.progress_heartbeat_fresh(
        _job(locked_at=now + timedelta(seconds=1)), now=now
    )
    assert not task_progress.progress_heartbeat_fresh(_job(locked_at=None), now=now)
    assert not task_progress.progress_heartbeat_fresh(
        _job(locked_at=datetime(2026, 11, 20, 11, 59, 30)), now=now
    )


def test_terminal_error_keeps_attempt_snapshot_available():
    job = _job(status="failed", locked_at=None, result={"attempts": {}})
    snapshot = {
        "phase": "analysis",
        "first": 0,
        "total": 1,
        "completed": 0,
        "current": None,
        "items": [{"source_id": 8, "name": "Source", "state": "done", "error": True}],
    }

    original = task_progress.read_snapshot
    task_progress.read_snapshot = lambda result, attempts: snapshot
    try:
        projected = task_progress.project_run(job, SimpleNamespace(job_type="analyze"))
    finally:
        task_progress.read_snapshot = original

    assert projected is not None
    assert projected["state"] == "failed"
    assert projected["error"] is True


def test_stale_running_snapshot_is_not_projected_as_current(monkeypatch):
    job = _job(locked_at=datetime.now(timezone.utc) - timedelta(seconds=91))
    monkeypatch.setattr(
        task_progress,
        "read_snapshot",
        lambda *_args: (_ for _ in ()).throw(AssertionError("stale snapshot was read")),
    )

    assert task_progress.project_run(job, SimpleNamespace(job_type="analyze")) is None


def test_current_attempt_reader_passes_job_attempt_and_rejects_stale_job(monkeypatch):
    captured = {}

    def snapshot(result, attempts):
        captured.update(result=result, attempts=attempts)
        return {"phase": "analysis", "first": 0, "total": 0, "completed": 0, "items": []}

    monkeypatch.setattr(task_progress, "read_snapshot", snapshot)
    job = _job(locked_at=datetime.now(timezone.utc) - timedelta(seconds=10), attempts=17, result={"attempt": "current"})
    # The reader uses the live guard and must preserve the Job attempt number.
    assert task_progress.progress_heartbeat_fresh(job)
    assert task_progress.current_attempt_snapshot(job) is not None
    assert captured["attempts"] == 17


def test_detail_template_keeps_stale_warning_but_not_progressbar():
    env = Environment(
        loader=FileSystemLoader(ROOT / "app/web/templates"),
        autoescape=select_autoescape(("html",)),
    )
    env.filters["human_dt"] = lambda value, **_kwargs: "сегодня"
    template = env.get_template("web/task_run_progress.html")
    output = template.render(
        task=SimpleNamespace(id=21, job_type="analyze", last_run_at=None),
        job=SimpleNamespace(id=31, status="running", created_at=datetime.now(timezone.utc)),
        summary={
            "status": "running",
            "heartbeat_unconfirmed": True,
            "label": "Выполнение задачи",
            "headline": None,
        },
        run_progress={
            "state": "running",
            "phases": [{"label": "Анализ", "processed": 4, "total": 10, "percent": 40, "segments": []}],
        },
        sources=[],
        can_view_sources=True,
        running_job_id=31,
        _tz="UTC",
    )

    assert "Нет свежего подтверждения" in output
    assert 'role="progressbar"' not in output
    assert "40%" not in output


def test_task_routes_order_every_current_job_selection_deterministically():
    source = (ROOT / "app/web/task_routes.py").read_text(encoding="utf-8")
    assert source.count(".order_by(Job.created_at.desc(), Job.id.desc())") >= 4
    assert ".order_by(Job.created_at.desc()).first()" not in source


def test_presenter_uses_same_freshness_guard_for_live_snapshot(monkeypatch):
    job = _job(locked_at=datetime.now(timezone.utc) - timedelta(seconds=91))
    monkeypatch.setattr(task_progress, "read_snapshot", lambda *_args: (_ for _ in ()).throw(AssertionError()))

    assert task_presenters._progress_view(job) is None


def test_detail_fallback_orders_active_jobs_outside_recent_window():
    source = (ROOT / "app/web/task_routes.py").read_text(encoding="utf-8")
    detail = source.split("async def task_detail(", 1)[1].split("async def task_latest_progress(", 1)[0]
    fallback = detail.split("# The running job may be outside the recent-10 window.", 1)[1].split("if running_job is not None:", 1)[0]
    assert 'status__in=["pending", "running"]' in fallback
    assert '.order_by(Job.created_at.desc(), Job.id.desc()).first()' in fallback
