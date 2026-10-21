"""Run-status modal summary: progress, cancel options and cancelled outcome (no DB)."""

from app.web.tasks import _job_summary


def _job(**fields):
    base = {"job_type": "collect", "status": "running", "error": None, "result": None, "attempts": 1,
            "created_at": None, "started_at": None}
    base.update(fields)
    return type("J", (), base)()


def test_running_collect_offers_content_choice_and_progress():
    from app.jobs.progress import build_snapshot

    sources = [type("S", (), {"id": 1, "name": "Канал"})(), type("S", (), {"id": 2, "name": "Блог"})()]
    snap = build_snapshot(sources, 1, [{"source_id": 1, "outcome": "collected", "items": 3, "new_items": 2}])
    s = _job_summary(_job(result={"progress": {**snap, "attempt": 1}}))
    assert s["activity"]
    assert s["cancel"]["kind"] == "running"
    assert [c["value"] for c in s["cancel"]["content_choices"]] == ["keep", "discard"]
    assert s["progress"]["completed"] == 1 and s["progress"]["total"] == 2
    assert s["progress"]["steps"][0]["note"] == "2 новых"
    assert s["progress"]["steps"][1]["state"] == "running"


def test_progress_of_an_older_attempt_is_not_shown():
    from app.jobs.progress import build_snapshot

    snap = build_snapshot([type("S", (), {"id": 1, "name": "A"})()], 0, [])
    s = _job_summary(_job(attempts=2, result={"progress": {**snap, "attempt": 1}}))
    assert "progress" not in s


def test_analyze_cancel_has_no_delete_option():
    s = _job_summary(_job(job_type="analyze"))
    assert s["cancel"]["kind"] == "running"
    assert "content_choices" not in s["cancel"]


def test_pending_cancel_before_start_and_retry_is_unavailable():
    assert _job_summary(_job(status="pending", attempts=0))["cancel"]["kind"] == "before_start"
    assert _job_summary(_job(status="pending", attempts=1))["cancel"]["kind"] == "unavailable"


def test_cancelled_runs_are_their_own_outcome():
    s = _job_summary(_job(status="failed", error="Cancelled by operator",
                          result={"cancellation": {"discard_collected": True, "discarded": 4, "settled": True}}))
    assert s["status"] == "cancelled"
    assert any("4" in note for note in s["notes"])
    before = _job_summary(_job(status="failed", error="Cancelled before start"))
    assert before["status"] == "cancelled"
    assert before["label"] == "Отменено до начала"


def test_other_failures_keep_the_error_view():
    s = _job_summary(_job(status="failed", error="boom"))
    assert s["status"] == "failed" and s["error"] == "boom"
