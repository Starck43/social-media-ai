"""Only verified, fresh per-source progress can be rendered in task cards."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from app.web.task_list_progress import task_list_progress

__source_only__ = True
NOW = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)


def job(**changes):
    data = dict(status="running", job_type="collect", locked_at=NOW,
                attempts=2, result={"progress": {"attempt": 2, "total": 5, "completed": 2, "items": []}})
    data.update(changes)
    return SimpleNamespace(**data)


def test_fresh_current_snapshot_returns_processed_sources_percent():
    assert task_list_progress(job(), now=NOW) == 40
    assert task_list_progress(job(result={"progress": {"attempt": 2, "total": 3, "completed": 0, "items": []}}), now=NOW) == 0


@pytest.mark.parametrize("change", [
    {"status": "pending"}, {"job_type": "other"}, {"locked_at": None},
    {"locked_at": NOW - timedelta(seconds=91)}, {"locked_at": NOW + timedelta(seconds=1)},
    {"locked_at": NOW.replace(tzinfo=None)}, {"attempts": 3}, {"result": None},
    {"result": {"progress": {"attempt": 2, "total": 0, "completed": 0, "items": []}}},
    {"result": {"progress": {"attempt": 2, "total": 2, "completed": 3, "items": []}}},
])
def test_unknown_or_unconfirmed_never_becomes_fake_progress(change):
    assert task_list_progress(job(**change), now=NOW) is None
