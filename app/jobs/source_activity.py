"""Shared collect/analyze occupancy semantics; no application or DB imports."""

from __future__ import annotations

from typing import Any

SOURCE_JOB_TYPES = frozenset({"collect", "analyze"})
ACTIVE_JOB_STATUSES = frozenset({"pending", "running"})


class SourceJobBusy(RuntimeError):
    """Admission found an existing job; it is not a newly admitted run."""

    def __init__(self, job: Any):
        self.job = job
        super().__init__(f"Source work is already pending or running (job #{job.id})")


def job_source_ids(payload: Any, task_id: int | None, task_sources: dict[int, set[int]], active: set[int]) -> set[int]:
    """Match handler precedence: linked task sources, all active, then payload.

    A missing/deleted task falls back to the payload, just like the worker.
    Malformed legacy payloads conservatively occupy all active sources.
    """
    plan = payload.get("task_plan") if isinstance(payload, dict) else None
    scope = plan.get("source_scope") if isinstance(plan, dict) else None
    if scope in {"selected", "discovery", "all"}:
        # New-plan admission stores the authorized source snapshot on the job.
        # Use it instead of every source historically linked to the task. A
        # discovery plan may add reusable sources only after admission, so its
        # unknown future set conservatively reserves the active workspace.
        if scope == "discovery":
            return active
        raw = payload.get("source_ids")
        if isinstance(raw, list) and all(type(value) is int and value > 0 for value in raw) and raw:
            return set(raw)
        if scope == "selected":
            selected = plan.get("source_ids")
            if isinstance(selected, list) and selected and all(type(value) is int and value > 0 for value in selected):
                return set(selected)
        return active
    if task_id is not None and task_id in task_sources:
        return task_sources[task_id] or active
    raw = payload.get("source_ids") if isinstance(payload, dict) else None
    if not raw or not isinstance(raw, list):
        return active
    if any(type(value) is not int or value <= 0 for value in raw):
        return active
    return set(raw)


def source_conflict(payload, task_id, jobs, task_sources, active, ignore_task_id=None):
    """Find an active overlapping collect/analyze job, independent of windows.

    A busy source is deliberately conservative even when scenarios/periods differ:
    retain the requested task occurrence rather than silently coalescing its work.
    `ignore_task_id` is the manual-re-run case: that task's own unfinished jobs
    are about to be replaced and must not count as a foreign occupant.
    """
    requested = job_source_ids(payload, task_id, task_sources, active)
    for job in jobs:
        if job.status not in ACTIVE_JOB_STATUSES or job.job_type not in SOURCE_JOB_TYPES:
            continue
        if ignore_task_id is not None and job.agent_task_id == ignore_task_id:
            continue
        if task_id is not None and job.agent_task_id == task_id:
            return job
        if requested & job_source_ids(job.payload, job.agent_task_id, task_sources, active):
            return job
    return None
