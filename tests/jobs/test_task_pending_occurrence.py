"""Task edits, pauses, deletions and admissions keep the queued occurrence honest.

A scheduled task holds at most one queued occurrence, and that pending row
carries the payload/schedule snapshot taken at admission time. When the task
definition changes, the task is deactivated, or the task is deleted, the stale
queued row must not execute the old plan — while claimed (`running`) and
terminal rows stay as history, and another task's queue is never touched.
A pending row whose previous attempt left an unconfirmed external outcome is
retained for review instead of being dropped.

Workspace-level job types (digest/prune/learn/reflect) also answer a scheduled
tick that arrives while their occurrence is still pending/running with a busy
receipt — no duplicate row, no schedule advance — and a manual re-run of an
`@once` task replaces its unfinished rows instead of adding a second copy.

Real PostgreSQL through the shared root conftest; strictly sequential.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.permissions import service_permission_scope
from app.core.tenant_context import tenant_scope
from app.jobs.recovery_policy import OUTCOME_UNCONFIRMED_PREFIX
from app.models import AgentTask, Job, Tenant
from app.models.managers.agent_task_manager import AgentTaskManager

pytestmark = pytest.mark.tenancy

CRON = "0 9 * * *"


def utc_now() -> datetime:
    """Aware UTC timestamp without microseconds (exact timestamptz round-trip)."""
    return datetime.now(timezone.utc).replace(microsecond=0)


async def make_task(
    tenant: Tenant,
    *,
    cron_expr: str = CRON,
    next_run_at: datetime | None = None,
    is_active: bool = True,
) -> AgentTask:
    with tenant_scope(tenant.id), service_permission_scope("agenttask", "create"):
        return await AgentTask.objects.create(
            name=f"occurrence-{uuid4().hex}",
            job_type="digest",
            cron_expr=cron_expr,
            payload={"period": "day"},
            is_active=is_active,
            next_run_at=next_run_at,
        )


async def make_job(tenant: Tenant, task: AgentTask, *, status: str = "pending", error: str | None = None) -> Job:
    stamp = utc_now()
    with tenant_scope(tenant.id):
        return await Job.objects.create(
            job_type=task.job_type,
            payload={"period": "day"},
            agent_task_id=task.id,
            status=status,
            error=error,
            run_at=stamp,
            attempts=1 if status == "running" else 0,
            started_at=stamp if status == "running" else None,
            locked_at=stamp if status == "running" else None,
            finished_at=stamp if status == "done" else None,
        )


async def task_jobs(tenant: Tenant, task: AgentTask) -> list[Job]:
    with tenant_scope(tenant.id):
        return list(await Job.objects.filter(agent_task_id=task.id))


async def task_job_ids(tenant: Tenant, task: AgentTask) -> set[int]:
    return {job.id for job in await task_jobs(tenant, task)}


async def stored_task(tenant: Tenant, task: AgentTask) -> AgentTask | None:
    with tenant_scope(tenant.id):
        return await AgentTask.objects.get(id=task.id)


async def update_task(tenant: Tenant, task: AgentTask, **changes) -> AgentTask | None:
    with tenant_scope(tenant.id), service_permission_scope("agenttask", "update"):
        return await AgentTask.objects.update_by_id(task.id, **changes)


@pytest.fixture
async def workspace():
    tenant = await Tenant.objects.create(
        name="Pending occurrence test", slug=f"occurrence-{uuid4().hex}", plan="business"
    )
    try:
        yield tenant
    finally:
        with tenant_scope(tenant.id):
            await Job.objects.delete()
        await Tenant.objects.delete_by_id(tenant.id)


async def test_editing_an_active_recurring_task_drops_only_its_stale_pending_job(workspace):
    """A definition change invalidates this task's queued row, nothing else."""
    tenant = workspace
    task = await make_task(tenant, next_run_at=utc_now() - timedelta(minutes=1))
    neighbour = await make_task(tenant, next_run_at=utc_now() - timedelta(minutes=1))
    stale = await make_job(tenant, task)
    neighbour_pending = await make_job(tenant, neighbour)
    running = await make_job(tenant, task, status="running")
    done = await make_job(tenant, task, status="done")

    rescheduled = utc_now() + timedelta(days=1)
    updated = await update_task(
        tenant, task, cron_expr="30 9 * * *", next_run_at=rescheduled, payload={"period": "week"}
    )
    assert updated is not None
    assert updated.cron_expr == "30 9 * * *"
    assert updated.next_run_at == rescheduled
    assert updated.payload == {"period": "week"}

    survivors = {job.id: job.status for job in await task_jobs(tenant, task)}
    assert stale.id not in survivors
    assert survivors == {running.id: "running", done.id: "done"}
    assert await task_job_ids(tenant, neighbour) == {neighbour_pending.id}


async def test_deactivating_a_task_drops_its_pending_job_and_keeps_claims_and_history(workspace):
    tenant = workspace
    task = await make_task(tenant, next_run_at=utc_now() - timedelta(minutes=1))
    pending = await make_job(tenant, task)
    running = await make_job(tenant, task, status="running")
    done = await make_job(tenant, task, status="done")

    updated = await update_task(tenant, task, is_active=False)
    assert updated is not None and updated.is_active is False

    ids = await task_job_ids(tenant, task)
    assert pending.id not in ids
    assert ids == {running.id, done.id}


async def test_deleting_a_task_removes_its_pending_job_and_leaves_no_ghost_rows(workspace):
    """The job FK is ON DELETE SET NULL: without the cleanup a queued run survives the task."""
    tenant = workspace
    task = await make_task(tenant, next_run_at=utc_now() - timedelta(minutes=1))
    filtered = await make_task(tenant, next_run_at=utc_now() - timedelta(minutes=1))
    await make_job(tenant, task)
    await make_job(tenant, filtered)

    with tenant_scope(tenant.id), service_permission_scope("agenttask", "delete"):
        assert await AgentTask.objects.delete_by_id(task.id) is True
        assert await AgentTask.objects.delete(id=filtered.id) == 1

    assert await task_job_ids(tenant, task) == set()
    assert await task_job_ids(tenant, filtered) == set()
    assert await stored_task(tenant, task) is None
    # No row may survive with a NULLed agent_task_id either: the tenant held
    # only these two tasks' jobs, so its whole queue must now be empty.
    with tenant_scope(tenant.id):
        assert await Job.objects.count() == 0


async def test_a_pending_row_with_an_unconfirmed_outcome_is_not_dropped_by_an_edit(workspace):
    """An unconfirmed external outcome needs a human decision, not a silent drop."""
    tenant = workspace
    task = await make_task(tenant, next_run_at=utc_now() - timedelta(minutes=1))
    unconfirmed_error = OUTCOME_UNCONFIRMED_PREFIX + "queue outcome needs operator review"
    retained = await make_job(tenant, task, error=unconfirmed_error)
    stale = await make_job(tenant, task)

    await update_task(tenant, task, cron_expr="30 9 * * *")

    assert await task_job_ids(tenant, task) == {retained.id}
    with tenant_scope(tenant.id):
        stored = await Job.objects.get(id=retained.id)
    assert stored.status == "pending"
    assert stored.error == unconfirmed_error


async def test_scheduled_admission_of_a_busy_workspace_task_returns_busy_without_duplicating(workspace):
    tenant = workspace
    due = utc_now() - timedelta(minutes=5)
    task = await make_task(tenant, next_run_at=due)
    queued = await make_job(tenant, task)

    with tenant_scope(tenant.id):
        receipt = await AgentTaskManager()._admit_task_run(
            task.id,
            now=due + timedelta(minutes=1),
            scheduled=True,
            expected_next_run_at=task.next_run_at,
        )

    assert receipt is not None
    assert receipt["status"] == "busy"
    assert receipt["task_id"] == task.id
    assert receipt["job"].id == queued.id

    jobs = await task_jobs(tenant, task)
    assert [job.id for job in jobs] == [queued.id]
    assert jobs[0].status == "pending"
    summary = await stored_task(tenant, task)
    assert summary is not None and summary.next_run_at == due, "a busy receipt must not advance the schedule"


async def test_two_manual_superseding_runs_of_a_once_task_leave_one_pending_job(workspace):
    tenant = workspace
    task = await make_task(tenant, cron_expr="@once")
    first_at = utc_now()
    second_at = first_at + timedelta(seconds=1)

    with tenant_scope(tenant.id):
        first = await AgentTaskManager()._admit_task_run(task.id, now=first_at, supersede_unfinished=True)
        assert first["status"] == "enqueued"
        second = await AgentTaskManager()._admit_task_run(task.id, now=second_at, supersede_unfinished=True)
    assert second["status"] == "enqueued"

    jobs = await task_jobs(tenant, task)
    assert len(jobs) == 1, "the re-run must replace the queued row, not add a second one"
    assert jobs[0].status == "pending"
    assert jobs[0].id == second["job"].id
    assert jobs[0].id != first["job"].id
