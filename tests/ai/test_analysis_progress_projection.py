"""Pure projection of source analysis progress (`app/web/analysis_progress.py`).

No DB, no framework: `project_job` reads only what a `job` object exposes. These
tests lock the status/label/percent contract for known, partial, full, zero,
unknown and unconfirmed states, plus claims/heartbeat and attempt isolation.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.jobs.progress import build_snapshot
from app.web.analysis_progress import project_job

ANALYZE = "analyze"
COLLECT = "collect"
STALE = 120


def _job(**fields):
    base = {
        "job_type": ANALYZE, "status": "running", "error": None, "result": None,
        "attempts": 1, "created_at": None, "locked_at": None,
    }
    base.update(fields)
    return SimpleNamespace(**base)


def _source_job(result=None, **fields):
    return _job(status="done", result=result, attempts=fields.pop("attempts", 1), **fields)


def _per_source(source_id, total, processed, selected=None, name="Канал"):
    row = {"source_id": source_id, "name": name, "raw_total": total, "raw_processed": processed}
    if selected is not None:
        row["raw_selected"] = selected
    return row


def _snapshot_item(raw):
    source = SimpleNamespace(id=7, name="Канал", tenant_id=1)
    snap = build_snapshot([source], 0, [])
    snap["items"][0].update(raw)
    return {**snap, "attempt": 1}


def now() -> datetime:
    return datetime.now(timezone.utc)


# ── lifecycle states ──────────────────────────────────────────────────────────


def test_pending_stays_queued_with_unknown_progress():
    view = project_job(_job(status="pending"), 7)
    assert view["status"] == "pending"
    assert view["label"] == "В очереди"
    assert view["percent"] is None and view["total"] is None


def test_running_with_fresh_heartbeat_and_snapshot_renders_counts():
    result = {"progress": _snapshot_item({"raw_total": 20, "raw_processed": 5, "raw_selected": 10})}
    view = project_job(_job(status="running", locked_at=now(), result=result), 7)
    assert view["status"] == "running"
    assert view["label"] == "Выполняется"
    assert view["processed"] == 5 and view["total"] == 20 and view["percent"] == 25


def test_running_stale_heartbeat_is_unconfirmed_not_stopped():
    result = {"progress": _snapshot_item({"raw_total": 20, "raw_processed": 5})}
    stale = now() - timedelta(seconds=STALE)
    view = project_job(_job(status="running", locked_at=stale, result=result), 7)
    assert view["status"] == "unconfirmed"
    assert view["label"] == "Статус не подтверждён"


def test_running_missing_heartbeat_is_unconfirmed():
    result = {"progress": _snapshot_item({"raw_total": 5, "raw_processed": 0})}
    view = project_job(_job(status="running", locked_at=None, result=result), 7)
    assert view["status"] == "unconfirmed"


def test_collect_running_uses_collection_label():
    result = {"progress": _snapshot_item({"raw_total": 5, "raw_processed": 0})}
    view = project_job(_job(job_type=COLLECT, status="running", locked_at=now(), result=result), 7)
    assert view["status"] == "running"
    assert view["label"] == "Сбор данных"


def test_inline_collect_names_both_phases_without_fabricating_raw_coverage():
    result = {"progress": _snapshot_item({})}
    job = _job(job_type=COLLECT, status="running", locked_at=now(), result=result)
    job._analysis_inline = True
    view = project_job(job, 7)
    assert view["label"] == "Сбор и анализ"
    assert view["percent"] is None and view["total"] is None


def test_explicit_cancellation_is_stopped_not_unconfirmed():
    wire = _job(status="failed", error="Cancelled by operator", result=None)
    view = project_job(wire, 7)
    assert view["status"] == "cancelled"
    assert view["label"] == "Остановлено"


# ── done: known / partial / full / empty / unknown ────────────────────────────


def test_done_full_is_complete_with_100_percent():
    view = project_job(_source_job(result={"per_source": [_per_source(7, 10, 10)]}), 7)
    assert view["status"] == "done"
    assert view["label"] == "Завершено"
    assert view["percent"] == 100 and view["processed"] == 10 and view["total"] == 10


def test_done_partial_when_processed_below_total():
    view = project_job(_source_job(result={"per_source": [_per_source(7, 10, 4)]}), 7)
    assert view["status"] == "partial"
    assert view["label"] == "Обработано частично"
    assert view["percent"] == 40


def test_done_with_per_source_error_reads_as_failed_not_partial():
    """Regression: a fully failed run (0 из 13) must not read as 'Обработано частично'."""
    row = _per_source(7, 13, 0)
    row["error"] = True
    view = project_job(_source_job(result={"per_source": [row]}), 7)
    assert view["status"] == "failed"
    assert view["label"] == "Ошибка"
    assert view["percent"] == 0


def test_done_empty_when_total_is_zero_is_no_data_never_zero_percent():
    view = project_job(_source_job(result={"per_source": [_per_source(7, 0, 0)]}), 7)
    assert view["status"] == "empty"
    assert view["label"] == "Нет данных"
    assert view["percent"] is None and view["total"] == 0


def test_done_without_raw_counts_of_a_collect_run():
    # Collect does not track raw coverage; absence of counters is not malformed.
    view = project_job(_source_job(job_type=COLLECT, result={"per_source": []}), 7)
    assert view["status"] == "done"
    assert view["label"] == "Сбор завершён"
    assert view["total"] is None and view["percent"] is None


# ── malformed inputs yield unknown, never success or arbitrary text ───────────


def test_done_with_bool_as_count_keeps_lifecycle_without_coverage():
    view = project_job(_source_job(result={"per_source": [_per_source(7, True, 2)]}), 7)
    assert view["status"] == "done"
    assert view["label"] == "Завершено"
    assert view["total"] is None and view["percent"] is None
    assert view["processed"] == 2


def test_done_with_non_numeric_total_keeps_lifecycle_without_coverage():
    view = project_job(_source_job(result={"per_source": [_per_source(7, "12", 3)]}), 7)
    assert view["status"] == "done"
    assert view["total"] is None and view["percent"] is None


def test_done_with_float_total_keeps_lifecycle_without_coverage():
    view = project_job(_source_job(result={"per_source": [_per_source(7, 12.0, 3)]}), 7)
    assert view["status"] == "done"


def test_done_with_negative_count_keeps_lifecycle_without_coverage():
    view = project_job(_source_job(result={"per_source": [_per_source(7, -1, 0)]}), 7)
    assert view["status"] == "done"
    assert view["total"] is None


def test_done_with_huge_total_beyond_bound_keeps_lifecycle_without_coverage():
    view = project_job(_source_job(result={"per_source": [_per_source(7, 10**9 + 1, 0)]}), 7)
    assert view["status"] == "done"
    assert view["total"] is None


def test_done_with_processed_above_total_keeps_lifecycle_without_coverage():
    view = project_job(_source_job(result={"per_source": [_per_source(7, 5, 9)]}), 7)
    assert view["status"] == "done"
    assert view["processed"] is None


def test_malformed_per_source_shape_keeps_lifecycle_without_coverage():
    view = project_job(_source_job(result={"per_source": {"source_id": 7}}), 7)
    assert view["status"] == "done"
    assert view["percent"] is None


def test_unknown_raw_status_maps_to_fixed_label_not_arbitrary_text():
    for weird in ("weird", "СТАТУС", 42, None, ""):
        view = project_job(_job(status=weird), 7)
        assert view["status"] == "unknown"
        assert view["label"] == "Статус неизвестен"
        assert view["label"] not in (weird, "")


def test_done_source_without_optional_coverage_keeps_completed_lifecycle():
    view = project_job(_source_job(result={"per_source": [{"source_id": 7, "raw_total": "bad"}]}), 7)
    assert view["status"] == "done"
    assert view["percent"] is None and view["processed"] is None and view["total"] is None


# ── snapshots: attempt isolation, per-source selection, durability ────────────


def test_running_uses_the_per_source_row_not_another_sources():
    result = {
        "per_source": [_per_source(3, 9, 9)],
        "progress": _snapshot_item({"raw_total": 4, "raw_processed": 1}),
    }
    view = project_job(_job(status="done", result=result), 7)
    assert view["total"] == 4 and view["processed"] == 1


def test_completed_source_stays_completed_while_next_source_runs():
    source_a = SimpleNamespace(id=7, name="A", tenant_id=1)
    source_b = SimpleNamespace(id=8, name="B", tenant_id=1)
    snapshot = build_snapshot([source_a, source_b], 1, [_per_source(7, 10, 10)])
    result = {"progress": {**snapshot, "attempt": 1}}
    view = project_job(_job(status="running", locked_at=now(), result=result), 7)
    assert view["status"] == "done" and view["percent"] == 100


def test_waiting_source_stays_queued_while_another_source_runs():
    source_a = SimpleNamespace(id=7, name="A", tenant_id=1)
    source_b = SimpleNamespace(id=8, name="B", tenant_id=1)
    snapshot = build_snapshot([source_a, source_b], 0, [])
    result = {"progress": {**snapshot, "attempt": 1}}
    view = project_job(_job(status="running", locked_at=now(), result=result), 8)
    assert view["status"] == "pending" and view["percent"] is None


def test_running_uses_attempt_snapshot_instead_of_retry_result_counters():
    stale = _per_source(7, 99, 99, selected=99)
    snapshot = _snapshot_item({"raw_total": 12, "raw_processed": 3, "raw_selected": 5})
    view = project_job(
        _job(status="running", locked_at=now(), result={"per_source": [stale], "progress": snapshot}),
        7,
    )
    assert view["total"] == 12 and view["processed"] == 3 and view["percent"] == 25


def test_cancelled_uses_last_attempt_snapshot_instead_of_retry_result_counters():
    stale = _per_source(7, 99, 99, selected=99)
    snapshot = _snapshot_item({"raw_total": 12, "raw_processed": 3, "raw_selected": 5})
    view = project_job(
        _job(status="failed", error="Cancelled by operator", result={"per_source": [stale], "progress": snapshot}),
        7,
    )
    assert view["status"] == "cancelled"
    assert view["total"] == 12 and view["processed"] == 3 and view["percent"] == 25


def test_failed_without_current_snapshot_does_not_reuse_previous_attempt_counters():
    stale = _per_source(7, 99, 99, selected=99)
    view = project_job(_job(status="failed", result={"per_source": [stale]}), 7)
    assert view["status"] == "failed"
    assert view["total"] is None and view["processed"] is None and view["percent"] is None


def test_retry_pending_does_not_show_matching_previous_attempt_snapshot():
    snapshot = _snapshot_item({"raw_total": 12, "raw_processed": 3})
    view = project_job(_job(status="pending", result={"progress": snapshot}), 7)
    assert view["status"] == "pending"
    assert view["total"] is None and view["processed"] is None and view["percent"] is None


def test_cancelled_before_start_does_not_reuse_previous_attempt_snapshot():
    snapshot = _snapshot_item({"raw_total": 12, "raw_processed": 3, "raw_selected": 5})
    view = project_job(
        _job(status="failed", error="Cancelled before start", result={"progress": snapshot}), 7,
    )
    assert view["status"] == "cancelled"
    assert view["total"] is None and view["processed"] is None and view["percent"] is None


def test_old_attempt_snapshot_is_ignored():
    snap = _snapshot_item({"raw_total": 6, "raw_processed": 6})
    snap["attempt"] = 1
    view = project_job(_job(status="done", result={"progress": snap}, attempts=2), 7)
    assert view["total"] is None and view["processed"] is None
    assert view["percent"] is None


def test_cancelled_terminal_keeps_attempt_matched_snapshot():
    snap = _snapshot_item({"raw_total": 10, "raw_processed": 7})
    view = project_job(
        _job(status="failed", error="Cancelled by operator", result={"progress": snap}, attempts=1), 7
    )
    assert view["status"] == "cancelled"
    assert view["total"] == 10 and view["processed"] == 7 and view["percent"] == 70


def test_done_prefers_persisted_per_source_over_snapshot():
    result = {
        "progress": _snapshot_item({"raw_total": 50, "raw_processed": 1}),
        "per_source": [_per_source(7, 8, 8)],
    }
    view = project_job(_job(status="done", result=result), 7)
    assert view["total"] == 8 and view["processed"] == 8 and view["percent"] == 100


def test_projection_never_leaks_diagnostics_or_raw_markers():
    result = {
        "per_source": [_per_source(7, 3, 3, selected=2)],
        "diagnostics": {"secret": True},
        "raw": {"leak": True},
    }
    view = project_job(_job(status="done", result=result), 7)
    # `job_id` is the one execution handle the source panel needs (to open the
    # run-status modal); result-envelope diagnostics never pass through.
    assert set(view) == {"job_id", "job_type", "status", "label", "percent", "processed", "total"}
    assert "selected" not in view and "diagnostics" not in view and "leak" not in view


def test_projection_carries_bounded_job_id_for_the_source_panel():
    view = project_job(_job(status="pending", id=88), 7)
    assert view["job_id"] == 88
    for bad in (None, 0, -1, 2.0, "88", True, 10**10):
        view = project_job(_job(status="pending", id=bad), 7)
        assert view["job_id"] is None, f"malformed id {bad!r} must stay unknown"
