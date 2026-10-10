"""Prepared PostgreSQL stale-reap regressions; owner execution ONLY.

Requires the existing reviewed test_schema, shared conftest and sequential
pytest. No providers/senders/schema operations in these test bodies.
Concurrent cases use separate sessions inside ONE test, not parallel pytest.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import event, update

from app.core.database import async_session_maker
from app.core.tenant_context import TenantContextError, tenant_scope
from app.models import Job, Tenant
from app.models.managers import job_manager as manager_module
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
            tenants.append(await Tenant.objects.create(
                name="Stale reap test", slug=f"stale-reap-{uuid4().hex}", plan="business"
            ))
        yield tenants
    finally:
        for tenant in reversed(tenants):
            with tenant_scope(tenant.id):
                await Job.objects.delete()
            await Tenant.objects.delete_by_id(tenant.id)


async def make_job(tenant, *, status="running", locked_at=STALE):
    with tenant_scope(tenant.id):
        return await Job.objects.create(
            job_type="learn", payload={"test": "stale-reap"}, run_at=NOW,
            status=status, locked_at=locked_at, started_at=STALE,
            attempts=2, max_attempts=3, result={"audit": "retained"},
            error="retained_error", llm_cost=None,
        )


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
    assert row.status == "pending" and row.locked_at is None
    assert row.started_at == STALE and row.run_at == NOW
    assert row.attempts == 2 and row.max_attempts == 3
    assert row.result == {"audit": "retained"} and row.error == "retained_error"
    assert row.llm_cost is None and row.finished_at is None
    assert (await stored(other, foreign)).status == "running"
    with tenant_scope(own.id):
        assert await JobManager().reap_stale() == 0


@pytest.mark.parametrize("status,lease", [
    ("running", NOW - timedelta(minutes=30)),
    ("running", NOW),
    ("running", None),
    ("pending", STALE),
    ("done", STALE),
    ("failed", STALE),
])
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
        manager.filter = manager.filter(id__in=[job.id for job in jobs]).filter
        changed = await manager.reap_stale(timeout_minutes=5)
    assert changed == 2
    for tenant, job in zip(workspaces, jobs):
        assert (await stored(tenant, job)).status == "pending"


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
    assert (await stored(own, job)).status == "pending"


@pytest.mark.parametrize("replacement", [
    {"status": "done", "finished_at": NOW},
    {"locked_at": NOW},
])
async def test_reap_does_not_overwrite_concurrent_completion_or_heartbeat(workspaces, replacement):
    own = workspaces[0]
    job = await make_job(own)
    issued = asyncio.Event()
    reaper = None
    # Bind the observer to THIS session's connection, never a global engine.
    async with async_session_maker() as reap_session:
        connection = await reap_session.connection()

        def before_execute(conn, cursor, statement, parameters, context, executemany):
            if context.compiled is not None and context.compiled.isupdate:
                issued.set()

        event.listen(connection.sync_connection, "before_cursor_execute", before_execute)
        try:
            async with async_session_maker() as holder:
                async with holder.begin():
                    await holder.execute(
                        update(Job).where(Job.id == job.id, Job.tenant_id == own.id).values(**replacement)
                    )
                    with tenant_scope(own.id):
                        manager = JobManager()
                        # Use the same tenant-guarded queryset with a pinned session
                        # so the signal observes the exact reaper UPDATE.
                        manager.filter = manager.get_queryset(session=reap_session).filter
                        reaper = asyncio.create_task(manager.reap_stale())
                    await asyncio.wait_for(issued.wait(), timeout=5)
                    # Exiting holder.begin commits the competing row update.
                assert await asyncio.wait_for(asyncio.shield(reaper), timeout=5) == 0
        finally:
            if reaper is not None and not reaper.done():
                reaper.cancel()
                await asyncio.gather(reaper, return_exceptions=True)
            event.remove(connection.sync_connection, "before_cursor_execute", before_execute)
    row = await stored(own, job)
    for field, value in replacement.items():
        assert getattr(row, field) == value
    assert row.status == replacement.get("status", "running")
    assert row.locked_at == replacement.get("locked_at", STALE)