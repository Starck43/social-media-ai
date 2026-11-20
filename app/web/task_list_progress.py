"""Bounded, current per-source progress for the assignment list."""
from datetime import datetime, timezone
from typing import Any

from app.jobs.progress import read_snapshot


def task_list_progress(job: Any, *, now: datetime | None = None) -> int | None:
    """Return processed-source percentage only for a live, validated attempt.

    This is not an overall success or completion percentage. Unknown, queued,
    stale and mismatched attempts must never be rendered as zero progress.
    """
    if job.status != "running" or getattr(job, "job_type", None) not in {"collect", "analyze"}:
        return None
    heartbeat = getattr(job, "locked_at", None)
    now = now or datetime.now(timezone.utc)
    if not isinstance(heartbeat, datetime) or heartbeat.utcoffset() is None or now.utcoffset() is None:
        return None
    age = (now - heartbeat).total_seconds()
    if age < 0 or age > 90:
        return None
    snapshot = read_snapshot(getattr(job, "result", None), getattr(job, "attempts", None))
    if snapshot is None or not snapshot["total"]:
        return None
    return snapshot["completed"] * 100 // snapshot["total"]
