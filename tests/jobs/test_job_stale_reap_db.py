"""Prepared PostgreSQL stale-reap regressions; owner execution ONLY.

Requires the existing reviewed test_schema, shared conftest and sequential
pytest. No providers/senders/schema operations in these test bodies.
Concurrent cases use separate sessions inside ONE test, not parallel pytest.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text, update

from app.core import database as database_module
from app.core.database import async_session_maker
from app.core.permissions import service_permission_scope
from app.core.tenant_context import TenantContextError, tenant_scope
from app.jobs.attempt_budget import ATTEMPT_BUDGET_STOP_PREFIX
from app.jobs.claim_outcomes import JobClaim
from app.jobs.recovery_policy import OUTCOME_UNCONFIRMED_PREFIX, outcome_unconfirmed
from app.models import AgentTask, Job, Tenant
from app.models.managers import job_lifecycle as manager_module
from app.models.managers.job_manager import JobManager

pytestmark = pytest.mark.tenancy
NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
STALE = NOW - timedelta(minutes=31)


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


@pytest.fixture
async def workspaces(monkeypatch):
    monkeypatch.setattr(manager_module, "datetime", FixedDatetime)
    tenants = []
    try:
        for _ in range(2):
            tenants.append(
                await Tenant.objects.create(name="Stale reap test", slug=f"stale-reap-{uuid4().hex}", plan="business")
            )
        yield tenants
    finally:
        for tenant in reversed(tenants):
            with tenant_scope(tenant.id):
                await Job.objects.delete()
            await Tenant.objects.delete_by_id(tenant.id)


async def make_job(tenant, *, status="running", locked_at=STALE, task=None, attempts=2):
    with tenant_scope(tenant.id):
        return await Job.objects.create(
            job_type="learn",
            payload={"test": "stale-reap"},
            run_at=NOW,
            status=status,
            locked_at=locked_at,
            started_at=STALE,
            attempts=attempts,
            max_attempts=3,
            result={"audit": "retained"},
            error="retained_error",
            llm_cost=None,
            agent_task_id=None if task is None else task.id,
        )


TRIGGERED = STALE - timedelta(minutes=1)


async def make_task(tenant, *, last_run_at=TRIGGERED, last_status="queued", last_error=None):
    with tenant_scope(tenant.id), service_permission_scope("agenttask", "create"):
        return await AgentTask.objects.create(
            name=f"stale-reap-{uuid4().hex}",
            cron_expr="@once",
            job_type="learn",
            is_active=False,
            last_run_at=last_run_at,
            last_status=last_status,
            last_error=last_error,
        )


async def stored_task(tenant, task):
    with tenant_scope(tenant.id):
        return await AgentTask.objects.get(id=task.id)


async def stored(tenant, job):
    with tenant_scope(tenant.id):
        return await Job.objects.get(id=job.id)


async def test_reap_is_tenant_scoped_and_preserves_attempt_evidence(workspaces):
    own, other = workspaces
    stale = await make_job(own)
    foreign = await make_job(other)
    with tenant_scope(own.id):
        assert await JobManager().reap_stale() == 1
    row = await stored(own, stale)
    assert row.status == "failed" and row.locked_at == STALE
    assert row.started_at == STALE and row.run_at == NOW
    assert row.attempts == 2 and row.max_attempts == 3
    assert row.result == {"audit": "retained"} and row.error == OUTCOME_UNCONFIRMED_PREFIX + "retained_error"
    assert row.llm_cost is None and row.finished_at == NOW
    assert (await stored(other, foreign)).status == "running"
    with tenant_scope(own.id):
        assert await JobManager().reap_stale() == 0


@pytest.mark.parametrize(
    "status,lease",
    [
        ("running", NOW - timedelta(minutes=30)),
        ("running", NOW),
        ("running", None),
        ("pending", STALE),
        ("done", STALE),
        ("failed", STALE),
    ],
)
async def test_fresh_boundary_null_and_nonrunning_rows_are_unchanged(workspaces, status, lease):
    own = workspaces[0]
    job = await make_job(own, status=status, locked_at=lease)
    with tenant_scope(own.id):
        assert await JobManager().reap_stale() == 0
    row = await stored(own, job)
    assert row.status == status and row.locked_at == lease


async def test_custom_timeout_and_explicit_bypass(workspaces):
    jobs = []
    for tenant in workspaces:
        jobs.append(await make_job(tenant, locked_at=NOW - timedelta(minutes=6)))
    with tenant_scope(bypass=True):
        # Explicit bypass retains the worker's cross-workspace recovery scope.
        # Limit this test to its own IDs: shared test_schema may retain evidence
        # from earlier owner runs, even though pytest processes are sequential.
        manager = JobManager()
        ids = [job.id for job in jobs]
        manager._recovery_scope = lambda model: [*JobManager._recovery_scope(model), model.id.in_(ids)]
        changed = await manager.reap_stale(timeout_minutes=5)
    assert changed == 2
    for tenant, job in zip(workspaces, jobs):
        assert (await stored(tenant, job)).status == "failed"


async def test_missing_scope_fails_closed(workspaces):
    own = workspaces[0]
    job = await make_job(own)
    with tenant_scope():
        with pytest.raises(TenantContextError):
            await JobManager().reap_stale()
    assert (await stored(own, job)).status == "running"


async def test_two_reapers_count_one_transition(workspaces):
    own = workspaces[0]
    job = await make_job(own)
    with tenant_scope(own.id):
        counts = await asyncio.gather(JobManager().reap_stale(), JobManager().reap_stale())
    assert sorted(counts) == [0, 1]
    assert (await stored(own, job)).status == "failed"


@pytest.mark.parametrize(
    ("first", "expected_renewed", "expected_reaped", "expected_status"),
    [
        ("renew", True, 0, "running"),
        ("reap", False, 1, "failed"),
    ],
)
async def test_renewal_and_reaper_follow_committed_order_on_independent_connections(
    workspaces, monkeypatch, first, expected_renewed, expected_reaped, expected_status
):
    """Committed manager writes make the later competing predicate fail.

    The two real managers are pinned to distinct PostgreSQL backends. A committed
    heartbeat makes the later stale predicate false, while committed quarantine
    makes the immutable running claim ineligible for later renewal.
    """
    own = workspaces[0]
    job = await make_job(own)
    claim = JobClaim.capture(job)

    class ExistingSessionContext:
        def __init__(self, session):
            self.session = session

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    renewal_session = async_session_maker()
    reap_session = async_session_maker()
    renewal_pid = (await renewal_session.execute(text("select pg_backend_pid()"))).scalar_one()
    reap_pid = (await reap_session.execute(text("select pg_backend_pid()"))).scalar_one()
    assert renewal_pid != reap_pid
    await renewal_session.rollback()
    await reap_session.rollback()
    pinned = {"session": renewal_session}
    monkeypatch.setattr(database_module, "async_session_maker", lambda: ExistingSessionContext(pinned["session"]))

    async def renew():
        pinned["session"] = renewal_session
        with tenant_scope(own.id):
            renewal_time = FixedDatetime.fromtimestamp(NOW.timestamp(), timezone.utc)
            return await JobManager().renew_claim(claim, now=renewal_time)

    async def reap():
        pinned["session"] = reap_session
        with tenant_scope(own.id):
            manager = JobManager()
            manager._recovery_scope = lambda model: [*JobManager._recovery_scope(model), model.id == job.id]
            return await manager.reap_stale()

    calls = {"renew": renew, "reap": reap}
    second = "reap" if first == "renew" else "renew"
    try:
        results = {first: await calls[first](), second: await calls[second]()}
    finally:
        await renewal_session.close()
        await reap_session.close()

    assert results == {"renew": expected_renewed, "reap": expected_reaped}
    row = await stored(own, job)
    assert row.status == expected_status
    if expected_status == "running":
        assert row.locked_at == NOW and row.finished_at is None
        assert row.error == "retained_error"
    else:
        assert row.locked_at == STALE and row.finished_at == NOW
        assert row.error == OUTCOME_UNCONFIRMED_PREFIX + "retained_error"
    assert row.started_at == STALE and row.attempts == 2
    assert row.result == {"audit": "retained"} and row.llm_cost is None


async def lock_waiters():
    """Backends blocked on a row lock while updating jobs (observer session only)."""
    async with async_session_maker() as observer:
        return (
            await observer.execute(
                text(
                    "select count(*) from pg_stat_activity where datname = current_database() "
                    "and wait_event_type = 'Lock' and query ilike 'update%jobs%'"
                )
            )
        ).scalar_one()


@pytest.mark.parametrize(
    "replacement",
    [
        {"status": "done", "finished_at": NOW},
        {"locked_at": NOW},
    ],
)
async def test_reap_does_not_overwrite_concurrent_completion_or_heartbeat(workspaces, replacement):
    own = workspaces[0]
    job = await make_job(own)
    reaper = None
    try:
        async with async_session_maker() as holder:
            async with holder.begin():
                await holder.execute(update(Job).where(Job.id == job.id, Job.tenant_id == own.id).values(**replacement))
                with tenant_scope(own.id):
                    manager = JobManager()
                    manager._recovery_scope = lambda model: [*JobManager._recovery_scope(model), model.id == job.id]
                    reaper = asyncio.create_task(manager.reap_stale())
                # The reaper's UPDATE must actually wait on the held row before the competing commit.
                for _ in range(100):
                    if await lock_waiters():
                        break
                    await asyncio.sleep(0.05)
                else:
                    pytest.fail("reaper update did not wait on the held row lock")
                # Exiting holder.begin commits the competing row update.
        assert await asyncio.wait_for(asyncio.shield(reaper), timeout=5) == 0
    finally:
        if reaper is not None and not reaper.done():
            reaper.cancel()
            await asyncio.gather(reaper, return_exceptions=True)
    row = await stored(own, job)
    for field, value in replacement.items():
        assert getattr(row, field) == value
    assert row.status == replacement.get("status", "running")
    assert row.locked_at == replacement.get("locked_at", STALE)


async def test_quarantine_projects_unconfirmed_outcome_onto_queued_task(workspaces):
    """A worker that died mid-run must not leave its Task reported as queued."""
    own = workspaces[0]
    task = await make_task(own)
    job = await make_job(own, task=task)
    with tenant_scope(own.id):
        assert await JobManager().reap_stale() == 1
    row = await stored(own, job)
    assert row.status == "failed" and row.error == OUTCOME_UNCONFIRMED_PREFIX + "retained_error"
    summary = await stored_task(own, task)
    assert summary.last_status == "failed"
    assert summary.last_error == row.error and outcome_unconfirmed(summary.last_error)
    assert summary.last_run_at == NOW


async def test_budget_stop_projects_operational_stop_onto_task(workspaces):
    own = workspaces[0]
    task = await make_task(own)
    job = await make_job(own, status="pending", locked_at=None, task=task, attempts=3)
    with tenant_scope(own.id):
        assert await JobManager().reap_stale() == 0
    row = await stored(own, job)
    assert row.status == "failed" and row.error == ATTEMPT_BUDGET_STOP_PREFIX + "retained_error"
    summary = await stored_task(own, task)
    assert (summary.last_status, summary.last_error) == ("failed", row.error)
    assert outcome_unconfirmed(summary.last_error) and summary.last_run_at == NOW


@pytest.mark.parametrize(
    "newer",
    [
        {"last_status": "queued", "last_error": None},
        {"last_status": "ok", "last_error": None},
        {"last_status": "failed", "last_error": "newer_failure"},
    ],
)
async def test_newer_trigger_or_outcome_on_task_is_not_overwritten(workspaces, newer):
    """An old attempt's quarantine must not hide a run admitted or finished after it started."""
    own = workspaces[0]
    later = STALE + timedelta(minutes=5)
    task = await make_task(own, last_run_at=later, **newer)
    job = await make_job(own, task=task)
    with tenant_scope(own.id):
        assert await JobManager().reap_stale() == 1
    assert (await stored(own, job)).status == "failed"
    summary = await stored_task(own, task)
    assert (summary.last_status, summary.last_error, summary.last_run_at) == (
        newer["last_status"],
        newer["last_error"],
        later,
    )


async def test_task_projection_failure_rolls_back_the_quarantine(workspaces, monkeypatch):
    own = workspaces[0]
    task = await make_task(own)
    job = await make_job(own, task=task)

    async def broken(*args, **kwargs):
        raise RuntimeError("task_projection_failed")

    monkeypatch.setattr(JobManager, "_write_stopped_task_outcome", staticmethod(broken))
    with tenant_scope(own.id):
        with pytest.raises(RuntimeError, match="task_projection_failed"):
            await JobManager().reap_stale()
    row = await stored(own, job)
    assert row.status == "running" and row.error == "retained_error" and row.finished_at is None
    summary = await stored_task(own, task)
    assert (summary.last_status, summary.last_run_at) == ("queued", TRIGGERED)


async def test_tenant_scope_never_projects_another_workspace_task(workspaces):
    own, other = workspaces
    foreign_task = await make_task(other)
    foreign = await make_job(other, task=foreign_task)
    with tenant_scope(own.id):
        assert await JobManager().reap_stale() == 0
    assert (await stored(other, foreign)).status == "running"
    assert (await stored_task(other, foreign_task)).last_status == "queued"
