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

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.database import new_session
from app.core.permissions import service_permission_scope
from app.core.tenant_context import tenant_scope
from app.jobs.recovery_policy import OUTCOME_UNCONFIRMED_PREFIX
from app.models import AgentTask, Job, Platform, Source, Tenant
from app.models.managers.agent_task_manager import AgentTaskManager
from app.models.managers.job_manager import JobManager
from app.types import AgentActionType, BotTriggerType, SourceType

pytestmark = pytest.mark.tenancy

CRON = "0 9 * * *"

# Execution snapshot fields and a value that differs from the stored default.
SNAPSHOT_CHANGES = [
    pytest.param("trigger_type", BotTriggerType.KEYWORD_MATCH, id="trigger-type"),
    pytest.param("trigger_config", {"keywords": ["refund"]}, id="trigger-config"),
    pytest.param("action_type", AgentActionType.COMMENT, id="action-type"),
    pytest.param("rate_limit_per_hour", 30, id="rate-limit"),
    pytest.param("cooldown_seconds", 45, id="cooldown"),
    pytest.param("dry_run", True, id="dry-run"),
]

# Same fields persisted at creation, then written back unchanged.
SNAPSHOT_VALUES = [
    pytest.param("trigger_type", BotTriggerType.USER_MENTION, id="trigger-type"),
    pytest.param("trigger_config", {"usernames": ["operator"]}, id="trigger-config"),
    pytest.param("action_type", AgentActionType.REPLY, id="action-type"),
    pytest.param("rate_limit_per_hour", 7, id="rate-limit"),
    pytest.param("cooldown_seconds", 90, id="cooldown"),
    pytest.param("dry_run", True, id="dry-run"),
]


def utc_now() -> datetime:
    """Aware UTC timestamp without microseconds (exact timestamptz round-trip)."""
    return datetime.now(timezone.utc).replace(microsecond=0)


async def make_task(
    tenant: Tenant,
    *,
    cron_expr: str = CRON,
    next_run_at: datetime | None = None,
    is_active: bool = True,
    **fields: object,
) -> AgentTask:
    with tenant_scope(tenant.id), service_permission_scope("agenttask", "create"):
        return await AgentTask.objects.create(
            name=f"occurrence-{uuid4().hex}",
            job_type="digest",
            cron_expr=cron_expr,
            payload={"period": "day"},
            is_active=is_active,
            next_run_at=next_run_at,
            **fields,
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


async def make_source(tenant: Tenant, name: str) -> Source:
    platform = await Platform.objects.filter(is_active=True).first()
    assert platform is not None, "the test database seeds platforms"
    with tenant_scope(tenant.id), service_permission_scope("source", "create"):
        return await Source.objects.create(
            platform_id=platform.id,
            name=name,
            external_id=f"occurrence-{uuid4().hex}",
            source_type=SourceType.GROUP,
        )


async def mutate_sources(tenant: Tenant, task: AgentTask, kind: str, source_ids: list[int]) -> int:
    with tenant_scope(tenant.id), service_permission_scope("agenttask", "update"):
        manager = AgentTaskManager()
        if kind == "set":
            return await manager.set_sources(task.id, source_ids)
        return await manager.add_sources(task.id, source_ids)


async def make_source_task(tenant: Tenant) -> AgentTask:
    """A collect task: its queued occurrence carries an explicit source snapshot."""
    with tenant_scope(tenant.id), service_permission_scope("agenttask", "create"):
        return await AgentTask.objects.create(
            name=f"occurrence-{uuid4().hex}",
            job_type="collect",
            cron_expr=CRON,
            payload={},
            is_active=True,
            next_run_at=utc_now() + timedelta(days=1),
        )


async def make_source_occurrence(tenant: Tenant, task: AgentTask, source_ids: list[int]) -> Job:
    """The queued occurrence as admission would have snapshotted it."""
    with tenant_scope(tenant.id):
        return await Job.objects.create(
            job_type=task.job_type,
            payload={"source_ids": list(source_ids)},
            agent_task_id=task.id,
            status="pending",
            run_at=utc_now(),
        )


async def admit_manual(tenant: Tenant, task: AgentTask, *, supersede: bool = False) -> dict | None:
    with tenant_scope(tenant.id):
        return await AgentTaskManager()._admit_task_run(task.id, now=utc_now(), supersede_unfinished=supersede)


async def delete_task(tenant: Tenant, task: AgentTask) -> bool:
    with tenant_scope(tenant.id), service_permission_scope("agenttask", "delete"):
        return await AgentTask.objects.delete_by_id(task.id)


async def observe_lock_request(monkeypatch, entered: asyncio.Event) -> None:
    """Signal the moment a contender asks for the real workspace admission gate."""

    original = JobManager.lock_source_admission

    async def observed(session, requested_tenant_id):
        entered.set()
        await original(session, requested_tenant_id)

    monkeypatch.setattr(JobManager, "lock_source_admission", staticmethod(observed))


async def assert_still_blocked(waiter: asyncio.Task, *, timeout: float = 0.5) -> None:
    """The contender reached the lock request and no result arrived: it is waiting."""
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(waiter), timeout=timeout)
    assert not waiter.done()


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


@pytest.mark.parametrize("route", ["by-id", "filtered"])
async def test_each_delete_route_drops_the_stale_pending_row_and_keeps_history_and_neighbours(workspace, route):
    """`delete_by_id` and filtered `delete` clean the same queue scope, no more."""
    tenant = workspace
    task = await make_task(tenant, next_run_at=utc_now() - timedelta(minutes=1))
    pending = await make_job(tenant, task)
    unconfirmed_error = OUTCOME_UNCONFIRMED_PREFIX + "delete route needs operator review"
    unconfirmed = await make_job(tenant, task, error=unconfirmed_error)
    running = await make_job(tenant, task, status="running")
    done = await make_job(tenant, task, status="done")
    neighbour = await make_task(tenant, next_run_at=utc_now() - timedelta(minutes=1))
    neighbour_pending = await make_job(tenant, neighbour)
    other = await Tenant.objects.create(
        name="Pending occurrence delete neighbour", slug=f"occurrence-{uuid4().hex}", plan="business"
    )
    try:
        other_task = await make_task(other, next_run_at=utc_now() - timedelta(minutes=1))
        other_pending = await make_job(other, other_task)

        with tenant_scope(tenant.id), service_permission_scope("agenttask", "delete"):
            if route == "by-id":
                assert await AgentTask.objects.delete_by_id(task.id) is True
            else:
                assert await AgentTask.objects.delete(id=task.id) == 1

        with tenant_scope(tenant.id):
            assert await Job.objects.get(id=pending.id) is None, "the stale occurrence must not outlive the task"
            retained_unconfirmed = await Job.objects.get(id=unconfirmed.id)
            assert retained_unconfirmed is not None, "an unconfirmed outcome stays for review"
            assert retained_unconfirmed.status == "pending"
            assert retained_unconfirmed.error == unconfirmed_error
            assert retained_unconfirmed.agent_task_id is None
            for job in (running, done):
                retained = await Job.objects.get(id=job.id)
                assert retained is not None and retained.status == job.status, "claimed/terminal rows stay as history"
                assert retained.agent_task_id is None
            assert await Job.objects.filter(agent_task_id=None).count() == 3
        assert await stored_task(tenant, task) is None
        assert await task_job_ids(tenant, neighbour) == {neighbour_pending.id}
        assert await stored_task(tenant, neighbour) is not None
        assert await task_job_ids(other, other_task) == {other_pending.id}
        assert await stored_task(other, other_task) is not None
    finally:
        with tenant_scope(other.id):
            await Job.objects.delete()
        await Tenant.objects.delete_by_id(other.id)


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


@pytest.mark.parametrize(("field", "value"), SNAPSHOT_CHANGES)
async def test_changing_a_snapshot_field_drops_only_the_stale_pending_job(workspace, field, value):
    """Every execution-snapshot field belongs to the queued plan it invalidates."""
    tenant = workspace
    task = await make_task(tenant, next_run_at=utc_now() - timedelta(minutes=1))
    neighbour = await make_task(tenant, next_run_at=utc_now() - timedelta(minutes=1))
    stale = await make_job(tenant, task)
    retained = await make_job(tenant, task, error=OUTCOME_UNCONFIRMED_PREFIX + "snapshot field needs operator review")
    running = await make_job(tenant, task, status="running")
    done = await make_job(tenant, task, status="done")
    neighbour_pending = await make_job(tenant, neighbour)

    updated = await update_task(tenant, task, **{field: value})
    assert updated is not None
    assert getattr(updated, field) == value

    survivors = {job.id: job.status for job in await task_jobs(tenant, task)}
    assert stale.id not in survivors
    assert survivors == {retained.id: "pending", running.id: "running", done.id: "done"}
    assert await task_job_ids(tenant, neighbour) == {neighbour_pending.id}


@pytest.mark.parametrize(("field", "value"), SNAPSHOT_VALUES)
async def test_unchanged_snapshot_field_preserves_the_pending_job(workspace, field, value):
    tenant = workspace
    task = await make_task(tenant, next_run_at=utc_now() - timedelta(minutes=1), **{field: value})
    pending = await make_job(tenant, task)

    updated = await update_task(tenant, task, **{field: value})
    assert updated is not None
    assert await task_job_ids(tenant, task) == {pending.id}


@pytest.mark.parametrize(
    ("kind", "target", "drops_pending"),
    [
        pytest.param("set", "other", True, id="set-changed"),
        pytest.param("set", "linked", False, id="set-unchanged"),
        pytest.param("set", "empty", True, id="set-cleared"),
        pytest.param("add", "other", True, id="add-new"),
        pytest.param("add", "linked", False, id="add-existing"),
    ],
)
async def test_source_link_change_drops_the_stale_pending_job(workspace, kind, target, drops_pending):
    """A queued occurrence is stale when its source links would differ at run time."""
    tenant = workspace
    task = await make_source_task(tenant)
    linked = await make_source(tenant, "linked")
    other = await make_source(tenant, "other")
    await mutate_sources(tenant, task, "set", [linked.id])
    pending = await make_job(tenant, task)
    source_ids = {"other": [other.id], "linked": [linked.id], "empty": []}[target]
    expected = sorted(source_ids) if kind == "set" else sorted(set(source_ids) | {linked.id})

    changed = await mutate_sources(tenant, task, kind, source_ids)

    assert changed == len(set(source_ids)), "the manager reports the ids it was asked to link"
    with tenant_scope(tenant.id):
        assert await AgentTaskManager().get_sources(task.id) == expected, "membership is the requested one"
    remaining = await task_job_ids(tenant, task)
    if drops_pending:
        assert pending.id not in remaining
        assert remaining == set()
    else:
        assert remaining == {pending.id}


async def test_foreign_workspace_source_is_rejected_without_touching_links_or_occurrence(workspace):
    """A cross-workspace link would widen the task's scope: refuse it untouched."""
    tenant = workspace
    task = await make_source_task(tenant)
    linked = await make_source(tenant, "linked")
    await mutate_sources(tenant, task, "set", [linked.id])
    pending = await make_job(tenant, task)
    other = await Tenant.objects.create(
        name="Pending occurrence stranger", slug=f"occurrence-{uuid4().hex}", plan="business"
    )
    try:
        foreign = await make_source(other, "foreign")

        with pytest.raises(ValueError, match="same workspace"):
            await mutate_sources(tenant, task, "set", [foreign.id])
        with pytest.raises(ValueError, match="same workspace"):
            await mutate_sources(tenant, task, "add", [linked.id, foreign.id])
    finally:
        with tenant_scope(other.id):
            await Job.objects.delete()
        await Tenant.objects.delete_by_id(other.id)

    with tenant_scope(tenant.id):
        assert await AgentTaskManager().get_sources(task.id) == [linked.id], "the rejected links changed nothing"
    assert await task_job_ids(tenant, task) == {pending.id}, "a rejected mutation must not drop the occurrence"


async def test_bulk_update_invalidates_pending_occurrences_within_the_tenant_only(workspace):
    """Bulk edits keep the tenant guard and leave claimed/history/unconfirmed rows."""
    tenant = workspace
    first = await make_task(tenant, next_run_at=utc_now() - timedelta(minutes=1))
    second = await make_task(tenant, next_run_at=utc_now() - timedelta(minutes=1))
    await make_job(tenant, first)
    await make_job(tenant, second)
    retained = await make_job(tenant, first, error=OUTCOME_UNCONFIRMED_PREFIX + "bulk edit needs review")
    running = await make_job(tenant, first, status="running")
    done = await make_job(tenant, second, status="done")

    other = await Tenant.objects.create(
        name="Pending occurrence neighbour", slug=f"occurrence-{uuid4().hex}", plan="business"
    )
    try:
        other_task = await make_task(other, next_run_at=utc_now() - timedelta(minutes=1))
        other_pending = await make_job(other, other_task)

        with tenant_scope(tenant.id), service_permission_scope("agenttask", "update"):
            changed = await AgentTaskManager().update(dry_run=True)
        assert changed == 2

        assert await task_job_ids(tenant, first) == {retained.id, running.id}
        assert await task_job_ids(tenant, second) == {done.id}
        assert await task_job_ids(other, other_task) == {other_pending.id}
        for task in (first, second):
            stored = await stored_task(tenant, task)
            assert stored is not None and stored.dry_run is True
    finally:
        with tenant_scope(other.id):
            await Job.objects.delete()
        await Tenant.objects.delete_by_id(other.id)


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


async def test_saving_an_inactive_task_as_inactive_keeps_its_pending_occurrence(workspace):
    """Re-sending the pause flag is not a definition change: the queued row stays."""
    tenant = workspace
    task = await make_task(tenant, is_active=False, next_run_at=utc_now() - timedelta(minutes=1))
    pending = await make_job(tenant, task)

    updated = await update_task(tenant, task, is_active=False)
    assert updated is not None and updated.is_active is False

    assert await task_job_ids(tenant, task) == {pending.id}


async def test_a_name_only_edit_keeps_the_pending_occurrence(workspace):
    """Renaming a task does not change what the queued row would execute."""
    tenant = workspace
    task = await make_task(tenant, next_run_at=utc_now() - timedelta(minutes=1))
    pending = await make_job(tenant, task)

    updated = await update_task(tenant, task, name=f"renamed-{uuid4().hex}")
    assert updated is not None

    assert await task_job_ids(tenant, task) == {pending.id}


async def test_deletion_that_wins_the_row_lock_leaves_admission_with_nothing(workspace, monkeypatch):
    """Delete holds the gate first: the queued row and the task leave together.

    The deletion pauses after the real workspace gate and task-row lock but
    before the pending cleanup, so admission can only arrive as a contender.
    """
    tenant = workspace
    task = await make_task(tenant, next_run_at=utc_now() - timedelta(minutes=1))
    pending = await make_job(tenant, task)
    locked = asyncio.Event()
    release_deletion = asyncio.Event()
    original_lock = AgentTaskManager._lock_task_for_mutation

    async def gated_lock(self, session, task_id):
        current = await original_lock(self, session, task_id)
        if not locked.is_set():
            locked.set()
            await release_deletion.wait()
        return current

    monkeypatch.setattr(AgentTaskManager, "_lock_task_for_mutation", gated_lock)
    admission_entered = asyncio.Event()
    deletion = asyncio.create_task(delete_task(tenant, task))
    admission = None
    try:
        await asyncio.wait_for(locked.wait(), timeout=5)
        await observe_lock_request(monkeypatch, admission_entered)
        admission = asyncio.create_task(admit_manual(tenant, task))
        await asyncio.wait_for(admission_entered.wait(), timeout=5)
        await assert_still_blocked(admission)

        release_deletion.set()
        assert await asyncio.wait_for(deletion, timeout=10) is True
        assert await asyncio.wait_for(admission, timeout=10) is None
    finally:
        release_deletion.set()
        for waiter in (deletion, admission):
            if waiter is not None and not waiter.done():
                waiter.cancel()
        await asyncio.gather(
            *(waiter for waiter in (deletion, admission) if waiter is not None), return_exceptions=True
        )

    assert await stored_task(tenant, task) is None
    assert await task_job_ids(tenant, task) == set()
    with tenant_scope(tenant.id):
        assert await Job.objects.get(id=pending.id) is None
        assert await Job.objects.filter(agent_task_id=None).count() == 0, "no NULL-task ghost may survive"
        assert await Job.objects.count() == 0


async def test_admission_holding_its_transaction_makes_deletion_wait_then_clean_up(workspace, monkeypatch):
    """Admission inserts the queued row first; deletion waits and removes it.

    The barrier sits right after the real insert, while the admission
    transaction still holds the workspace gate and the task row lock.
    """
    tenant = workspace
    task = await make_task(tenant, next_run_at=utc_now() - timedelta(minutes=1))
    inserted = asyncio.Event()
    release_admission = asyncio.Event()
    original_enqueue = JobManager.enqueue
    paused = False

    async def gated_enqueue(self, *args, **kwargs):
        nonlocal paused
        job = await original_enqueue(self, *args, **kwargs)
        if not paused:
            paused = True
            inserted.set()
            await release_admission.wait()
        return job

    monkeypatch.setattr(JobManager, "enqueue", gated_enqueue)
    admission = asyncio.create_task(admit_manual(tenant, task))
    deletion = None
    try:
        await asyncio.wait_for(inserted.wait(), timeout=5)
        deletion_entered = asyncio.Event()
        await observe_lock_request(monkeypatch, deletion_entered)
        deletion = asyncio.create_task(delete_task(tenant, task))
        await asyncio.wait_for(deletion_entered.wait(), timeout=5)
        await assert_still_blocked(deletion)

        release_admission.set()
        receipt = await asyncio.wait_for(admission, timeout=10)
        assert receipt is not None and receipt["status"] == "enqueued"
        queued_id = receipt["job"].id
        assert await asyncio.wait_for(deletion, timeout=10) is True
    finally:
        release_admission.set()
        for waiter in (admission, deletion):
            if waiter is not None and not waiter.done():
                waiter.cancel()
        await asyncio.gather(
            *(waiter for waiter in (admission, deletion) if waiter is not None), return_exceptions=True
        )

    assert await stored_task(tenant, task) is None
    with tenant_scope(tenant.id):
        assert await Job.objects.get(id=queued_id) is None, "the queued occurrence must not outlive the task"
        assert await Job.objects.filter(agent_task_id=None).count() == 0
        assert await Job.objects.count() == 0


async def test_source_replacement_that_commits_wins_the_admission_race(workspace, monkeypatch):
    """A committed replacement is the snapshot admission must pick up.

    `set_sources` runs in its own real transaction and the test holds it open
    after the workspace gate and task lock, so admission can only wait at the
    gate until the replacement commits.
    """
    tenant = workspace
    task = await make_source_task(tenant)
    old = await make_source(tenant, "old")
    new_first = await make_source(tenant, "new-first")
    new_second = await make_source(tenant, "new-second")
    await mutate_sources(tenant, task, "set", [old.id])
    stale = await make_source_occurrence(tenant, task, [old.id])
    new_ids = sorted([new_first.id, new_second.id])

    session = new_session()
    admission = None
    try:
        await session.begin()
        with tenant_scope(tenant.id), service_permission_scope("agenttask", "update"):
            await AgentTaskManager().set_sources(task.id, new_ids, session=session)

        entered = asyncio.Event()
        await observe_lock_request(monkeypatch, entered)
        admission = asyncio.create_task(admit_manual(tenant, task))
        await asyncio.wait_for(entered.wait(), timeout=5)
        await assert_still_blocked(admission)

        await session.commit()
        receipt = await asyncio.wait_for(admission, timeout=10)
    finally:
        if session.in_transaction():
            await session.rollback()
        await session.close()
        if admission is not None and not admission.done():
            admission.cancel()
            await asyncio.gather(admission, return_exceptions=True)

    assert receipt["status"] == "enqueued"
    assert sorted(receipt["job"].payload["source_ids"]) == new_ids
    jobs = await task_jobs(tenant, task)
    assert [job.id for job in jobs] == [receipt["job"].id]
    assert stale.id not in {job.id for job in jobs}, "the replaced occurrence is stale"
    with tenant_scope(tenant.id):
        assert await AgentTaskManager().get_sources(task.id) == new_ids


async def test_source_replacement_that_rolls_back_keeps_old_membership_and_occurrence(workspace):
    """A failed replacement rolls its cleanup back with it.

    The retained pending row still occupies the source, so the operator-visible
    way to get a fresh snapshot is the manual replacement run, which must use
    exactly the old source ids that survived the rollback.
    """
    tenant = workspace
    task = await make_source_task(tenant)
    old = await make_source(tenant, "old")
    new = await make_source(tenant, "new")
    await mutate_sources(tenant, task, "set", [old.id])
    pending = await make_source_occurrence(tenant, task, [old.id])

    session = new_session()
    try:
        await session.begin()
        with tenant_scope(tenant.id), service_permission_scope("agenttask", "update"):
            await AgentTaskManager().set_sources(task.id, [new.id], session=session)
        await session.rollback()
    finally:
        await session.close()

    with tenant_scope(tenant.id):
        assert await AgentTaskManager().get_sources(task.id) == [old.id]
    assert await task_job_ids(tenant, task) == {pending.id}, "the cleanup rolled back with the mutation"

    receipt = await admit_manual(tenant, task, supersede=True)
    assert receipt is not None and receipt["status"] == "enqueued"
    assert receipt["job"].payload["source_ids"] == [old.id]
    jobs = await task_jobs(tenant, task)
    assert [job.id for job in jobs] == [receipt["job"].id]
    assert pending.id not in {job.id for job in jobs}
    with tenant_scope(tenant.id):
        assert await AgentTaskManager().get_sources(task.id) == [old.id]
