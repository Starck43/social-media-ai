"""Task execution orchestration shared by the web routes."""

from __future__ import annotations

from .task_forms import (
    _can_activate,
)
from .task_shared import (
    AgentTask,
    AgentTaskManager,
    Any,
)


async def run_task_now(task: "AgentTask") -> dict | None:
    """Run the task's job right now, in this process — it never sits in the queue.

    "Собрать сейчас" has to feel immediate, and a queued job is not: it waits for a
    worker, and if the worker is down or busy the user just sees a spinner that
    times out. So the job row is written and executed in the same call — the row
    still exists (it is the audit trail the result modal and `/app/jobs` read),
    but it is stamped `running` immediately, so the worker never picks it up.

    Returns the dispatcher's outcome dict plus `job_id` (the callers redirect to
    `/app/tasks?job_id=…` so the modal can render it).
    """
    from app.jobs.dispatcher import run_task_directly

    return await run_task_directly(task)


async def queue_task_now(task: "AgentTask", initiated_by_user_id: int | None = None) -> dict[str, Any]:
    """Enqueue the task's job and execute it in this process (no worker needed).

    "Сохранить и выполнить" used to call `run_task_now`, which executes the
    handler synchronously and only then returns. For a long job that is minutes
    of an open spinner with no way to tell a slow run from a hung one — an
    `analyze` task draining a backlog took over ten minutes, and the operator's
    natural reaction was to click again, which collided with the row the first
    click had already written.

    Queueing returns as soon as the job row exists, so the page reloads at once
    and the run-status modal (which already polls `/app/tasks/job/<id>/status`)
    follows the job to completion. The job executes in a background asyncio task
    in the same process (uvicorn event loop), so manual runs work even when
    `app.runtime` worker is not running (dev: uvicorn alone). A competing real
    worker may claim the job first (atomic claim) — either way the job runs.

    Returns the same `{job_id, status}` shape so both callers read alike.
    """
    from app.jobs.dispatcher import launch_job_background
    from app.jobs.enqueue import enqueue_task_run
    from app.jobs.source_activity import SourceJobBusy
    from app.models.managers.agent_task_manager import AgentTaskManager

    linked_sources = await AgentTaskManager().get_sources(task.id)
    blocked = await _can_activate(
        task.tenant_id, task.job_type, linked_sources, task.agent_scenario_id, payload=task.payload
    )
    if blocked is not None:
        return {"status": "blocked", "reason": blocked}

    if initiated_by_user_id is None:
        from app.core.permissions import get_current_user

        actor = get_current_user()
        initiated_by_user_id = getattr(actor, "id", None)

    try:
        job = await enqueue_task_run(
            task,
            extra_payload={"force_refresh": False, "force_reanalyze": False},
            initiated_by_user_id=initiated_by_user_id,
            supersede_unfinished=True,
        )
    except SourceJobBusy as busy:
        return {"job_id": busy.job.id, "status": busy.job.status, "already_running": True}

    # Execute in this process's event loop so the job runs without a worker.
    launch_job_background(job.id, allow_retry=False)
    return {"job_id": job.id, "status": job.status}


__all__ = [name for name in globals() if not name.startswith("__")]
