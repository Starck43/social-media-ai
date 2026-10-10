"""Run an agent task now: enqueue its job and record the trigger.

Single entry point for every surface that can start a task by hand — the CLI
(`task run`), the web UI ("Выполнить сейчас" / "Сохранить и выполнить") and the
sqladmin action. It used to be reimplemented three times with three different
behaviours (the CLI version did not record the trigger at all, so a `@once`
task stayed armed and a recurring one never advanced `last_run_at`).

Tenancy: the job and the bookkeeping writes must happen inside the *task's*
workspace. Callers pass the task row; the scope is taken from `task.tenant_id`.
The CLI resolves it the same way — the task row is the single source of truth
for "whose workflow is this".
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


async def enqueue_task_run(task: Any, extra_payload: dict[str, Any] | None = None) -> Any:
    """Enqueue a job for an existing task and mark the task as triggered.

    `extra_payload` merges one-off overrides (e.g. a manual `--force-refresh`)
    into the job's payload without persisting them on the task row.

    Returns the created `Job` so callers can track it (run it now, poll its
    status, show it in a modal).
    """
    from app.core.permissions import service_permission_scope
    from app.core.tenant_context import tenant_scope
    from app.models.managers.agent_task_manager import AgentTaskManager
    from app.models.managers.job_manager import JobManager

    with tenant_scope(task.tenant_id):
        payload = dict(task.payload or {})
        if extra_payload:
            payload.update({k: v for k, v in extra_payload.items() if v is not None})

        job = await JobManager().enqueue(
            job_type=task.job_type,
            payload=payload,
            agent_task_id=task.id,
            run_at=datetime.now(timezone.utc),
        )
        # Record the trigger (last_run_at) without disturbing the schedule of a
        # recurring task; a one-shot task is completed in the process. The real
        # outcome (last_status/last_error) is written by the worker via
        # record_result().
        tasks = AgentTaskManager()
        if task.cron_expr == "@once":
            await tasks.mark_triggered(task.id, None, status="ok")
            with service_permission_scope("agenttask", "update"):
                await tasks.update_by_id(task.id, is_active=False)
        else:
            await tasks.mark_triggered(task.id, task.next_run_at, status="ok")
        return job
