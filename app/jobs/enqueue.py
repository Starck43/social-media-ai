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
    from app.core.tenant_context import tenant_scope
    from app.models.managers.agent_task_manager import AgentTaskManager

    with tenant_scope(task.tenant_id):
        receipt = await AgentTaskManager()._admit_task_run(
            task.id, now=datetime.now(timezone.utc), extra_payload=extra_payload
        )
        if receipt is None:
            raise ValueError("Task run was not admitted for this workspace")
        return receipt["job"]
