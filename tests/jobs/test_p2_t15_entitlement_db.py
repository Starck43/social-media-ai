"""Real PostgreSQL validation for P2-T15 admission, isolation and quota locking."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, insert, select, text, update

from app.core.database import async_engine, async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import TenantContextError, tenant_scope
from app.jobs.enqueue import enqueue_task_run
from app.models import AgentTask, EntitlementUsage, Job, Platform, Source, Tenant
from app.models.audit.action_audit_event import ActionAuditEvent
from app.models.managers.entitlement_manager import EntitlementManager
from app.models.scheduling.agent_task import agent_task_sources
from app.services.tenancy.entitlements import EntitlementDenied
from app.services.tenancy.plan_catalog import PLAN_CATALOG, PlanGrant
from app.types import PlatformType, SourceType
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401
from tests.support.schema_guard import require_isolated_schema

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]

VK_READ = "connector.vk.read"
TG_READ = "connector.telegram.read"
NOW = datetime.now(timezone.utc)
LOCK_ID = 1370093


@pytest.fixture(scope="module", autouse=True)
async def _exclusive_database_writer():
    """Serialize this module with every cooperative local DB verifier."""
    async with async_engine.connect() as connection:
        acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_ID})
        if acquired is not True:
            pytest.fail(f"database writer advisory lock {LOCK_ID} is already held")
        try:
            yield
        finally:
            await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_ID})


@pytest.fixture
async def entitlement_world(personal_world):  # noqa: F811
    """Create only tagged rows; remove and verify every P2-T15-owned row."""
    require_isolated_schema()
    owned = NS(tasks=set(), jobs=set(), sources=set())
    tenant_ids = [tenant.id for tenant in personal_world.tenants]

    async def create_task(*, workspace=0, with_source=False):
        tenant = personal_world.tenants[workspace]
        tag = f"p2-t15-{uuid4().hex[:12]}"
        async with async_session_maker() as session, session.begin():
            task = AgentTask(
                tenant_id=tenant.id,
                name=tag,
                job_type="collect",
                cron_expr="@once",
                payload={},
                is_active=True,
            )
            session.add(task)
            await session.flush()
            owned.tasks.add(task.id)
            source = None
            if with_source:
                platform_id = await session.scalar(select(Platform.id).where(Platform.platform_type == PlatformType.VK))
                assert platform_id is not None
                source = Source(
                    tenant_id=tenant.id,
                    platform_id=platform_id,
                    name=tag,
                    source_type=SourceType.GROUP,
                    external_id=tag,
                    params={},
                    is_active=True,
                )
                session.add(source)
                await session.flush()
                owned.sources.add(source.id)
                await session.execute(insert(agent_task_sources).values(agent_task_id=task.id, source_id=source.id))
        return task, source

    async def create_job(*, workspace=0, task=None, status="running", actor=None, payload=None):
        tenant = personal_world.tenants[workspace]
        async with async_session_maker() as session, session.begin():
            job = Job(
                tenant_id=tenant.id,
                agent_task_id=getattr(task, "id", None),
                initiated_by_user_id=actor,
                job_type="collect",
                payload=payload or {},
                status=status,
                run_at=NOW,
            )
            session.add(job)
            await session.flush()
            owned.jobs.add(job.id)
        return job

    yield NS(
        base=personal_world,
        owned=owned,
        create_task=create_task,
        create_job=create_job,
    )

    async with async_session_maker() as session, session.begin():
        await session.execute(delete(EntitlementUsage).where(EntitlementUsage.tenant_id.in_(tenant_ids)))
        await session.execute(delete(Job).where(Job.tenant_id.in_(tenant_ids)))
        if owned.tasks:
            await session.execute(delete(agent_task_sources).where(agent_task_sources.c.agent_task_id.in_(owned.tasks)))
        if owned.sources:
            await session.execute(delete(Source).where(Source.id.in_(owned.sources)))
        if owned.tasks:
            await session.execute(delete(AgentTask).where(AgentTask.id.in_(owned.tasks)))
    async with async_session_maker() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(EntitlementUsage).where(EntitlementUsage.tenant_id.in_(tenant_ids))
            )
            == 0
        )
        assert await session.scalar(select(func.count()).select_from(Job).where(Job.id.in_(owned.jobs or {-1}))) == 0
        assert (
            await session.scalar(
                select(func.count()).select_from(AgentTask).where(AgentTask.id.in_(owned.tasks or {-1}))
            )
            == 0
        )
        assert (
            await session.scalar(select(func.count()).select_from(Source).where(Source.id.in_(owned.sources or {-1})))
            == 0
        )


async def _usage_rows(tenant_id):
    async with async_session_maker() as session:
        return list(
            (
                await session.execute(
                    select(EntitlementUsage)
                    .where(EntitlementUsage.tenant_id == tenant_id)
                    .order_by(EntitlementUsage.id)
                )
            )
            .scalars()
            .all()
        )


async def test_distinct_jobs_race_for_one_quota_and_only_one_is_admitted(entitlement_world, monkeypatch):
    tenant = entitlement_world.base.tenants[0]
    first = await entitlement_world.create_job(actor=entitlement_world.base.users[0].id)
    second = await entitlement_world.create_job(actor=entitlement_world.base.users[0].id)
    monkeypatch.setitem(PLAN_CATALOG, tenant.plan, PlanGrant(("*",), {VK_READ: 1}))

    with tenant_scope(tenant.id):
        outcomes = await asyncio.gather(
            EntitlementManager().admit_execution({VK_READ}, job_id=first.id),
            EntitlementManager().admit_execution({VK_READ}, job_id=second.id),
            return_exceptions=True,
        )

    allowed = [outcome for outcome in outcomes if not isinstance(outcome, BaseException)]
    denied = [outcome for outcome in outcomes if isinstance(outcome, EntitlementDenied)]
    rows = await _usage_rows(tenant.id)
    assert len(allowed) == 1
    assert len(denied) == 1
    assert denied[0].decision.denials == ({"capability": VK_READ, "reason": "quota_exhausted"},)
    assert len(rows) == 1
    assert rows[0].job_id in {first.id, second.id}


async def test_same_job_concurrent_rechecks_are_idempotent(entitlement_world, monkeypatch):
    tenant = entitlement_world.base.tenants[0]
    job = await entitlement_world.create_job(actor=entitlement_world.base.users[0].id)
    monkeypatch.setitem(PLAN_CATALOG, tenant.plan, PlanGrant(("*",), {VK_READ: 1}))

    with tenant_scope(tenant.id):
        outcomes = await asyncio.gather(
            *(EntitlementManager().admit_execution({VK_READ}, job_id=job.id) for _ in range(4))
        )

    assert all(outcome.allowed for outcome in outcomes)
    rows = await _usage_rows(tenant.id)
    assert [(row.job_id, row.capability) for row in rows] == [(job.id, VK_READ)]


async def test_usage_is_workspace_isolated_and_actor_is_copied_from_trusted_job_field(entitlement_world):
    first_tenant, second_tenant = entitlement_world.base.tenants
    first_actor, second_actor = entitlement_world.base.users[0], entitlement_world.base.users[2]
    first = await entitlement_world.create_job(
        workspace=0,
        actor=first_actor.id,
        payload={"initiated_by_user_id": second_actor.id},
    )
    second = await entitlement_world.create_job(
        workspace=1,
        actor=second_actor.id,
        payload={"initiated_by_user_id": first_actor.id},
    )

    with tenant_scope(first_tenant.id):
        await EntitlementManager().admit_execution({VK_READ}, job_id=first.id)
        with pytest.raises(TenantContextError, match="running job in this workspace"):
            await EntitlementManager().admit_execution({TG_READ}, job_id=second.id)
    with tenant_scope(second_tenant.id):
        await EntitlementManager().admit_execution({TG_READ}, job_id=second.id)
    with tenant_scope(first_tenant.id, bypass=True), pytest.raises(TenantContextError, match="concrete normal"):
        await EntitlementManager().inspect()

    first_rows = await _usage_rows(first_tenant.id)
    second_rows = await _usage_rows(second_tenant.id)
    assert [(row.job_id, row.initiated_by_user_id) for row in first_rows] == [(first.id, first_actor.id)]
    assert [(row.job_id, row.initiated_by_user_id) for row in second_rows] == [(second.id, second_actor.id)]


async def test_task_admission_is_read_only_then_queued_expiry_is_rechecked_at_execution(entitlement_world, monkeypatch):
    tenant = entitlement_world.base.tenants[0]
    actor = entitlement_world.base.users[0]
    task, _source = await entitlement_world.create_task(with_source=True)
    monkeypatch.setattr(ActionAuditEvent.objects, "record_success", AsyncMock(return_value=NS(id=1)))

    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(Tenant)
            .where(Tenant.id == tenant.id)
            .values(plan_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
    with permission_scope(actor), tenant_scope(tenant.id), pytest.raises(EntitlementDenied) as admission_denied:
        await enqueue_task_run(task, initiated_by_user_id=actor.id)
    assert admission_denied.value.decision.denials == ({"capability": VK_READ, "reason": "plan_expired"},)

    async with async_session_maker() as session:
        assert await session.scalar(select(func.count()).select_from(Job).where(Job.agent_task_id == task.id)) == 0
        assert (
            await session.scalar(
                select(func.count()).select_from(EntitlementUsage).where(EntitlementUsage.tenant_id == tenant.id)
            )
            == 0
        )

    async with async_session_maker() as session, session.begin():
        await session.execute(update(Tenant).where(Tenant.id == tenant.id).values(plan_expires_at=None))
    with permission_scope(actor):
        job = await enqueue_task_run(task, initiated_by_user_id=actor.id)
    entitlement_world.owned.jobs.add(job.id)
    assert job.status == "pending"
    assert await _usage_rows(tenant.id) == []

    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(Tenant)
            .where(Tenant.id == tenant.id)
            .values(plan_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        await session.execute(update(Job).where(Job.id == job.id).values(status="running"))
    with tenant_scope(tenant.id), pytest.raises(EntitlementDenied) as execution_denied:
        await EntitlementManager().admit_execution({VK_READ}, job_id=job.id)

    assert execution_denied.value.decision.denials == ({"capability": VK_READ, "reason": "plan_expired"},)
    assert await _usage_rows(tenant.id) == []


async def test_queued_plan_downgrade_is_rechecked_without_consuming_usage(entitlement_world, monkeypatch):
    tenant = entitlement_world.base.tenants[0]
    job = await entitlement_world.create_job(status="pending", actor=entitlement_world.base.users[0].id)
    monkeypatch.setitem(PLAN_CATALOG, "pro", PlanGrant((VK_READ,)))
    monkeypatch.setitem(PLAN_CATALOG, "starter", PlanGrant((TG_READ,)))

    async with async_session_maker() as session:
        with tenant_scope(tenant.id):
            preview = await EntitlementManager().evaluate(session, {VK_READ})
    assert preview.allowed is True

    async with async_session_maker() as session, session.begin():
        await session.execute(update(Tenant).where(Tenant.id == tenant.id).values(plan="starter"))
        await session.execute(update(Job).where(Job.id == job.id).values(status="running"))
    with tenant_scope(tenant.id), pytest.raises(EntitlementDenied) as denied:
        await EntitlementManager().admit_execution({VK_READ}, job_id=job.id)

    assert denied.value.decision.plan == "starter"
    assert denied.value.decision.denials == ({"capability": VK_READ, "reason": "not_granted"},)
    assert await _usage_rows(tenant.id) == []


async def test_usage_survives_job_and_task_pruning_then_cascades_with_workspace(entitlement_world):
    tenant = entitlement_world.base.tenants[0]
    task, _ = await entitlement_world.create_task()
    job = await entitlement_world.create_job(
        task=task,
        actor=entitlement_world.base.users[0].id,
    )
    with tenant_scope(tenant.id):
        await EntitlementManager().admit_execution({VK_READ}, job_id=job.id)
    usage = (await _usage_rows(tenant.id))[0]

    async with async_session_maker() as session, session.begin():
        await session.execute(delete(Job).where(Job.id == job.id))
        await session.execute(delete(AgentTask).where(AgentTask.id == task.id))
    retained = (await _usage_rows(tenant.id))[0]
    assert retained.id == usage.id
    assert retained.job_id == job.id
    assert retained.task_id == task.id
    assert retained.initiated_by_user_id == entitlement_world.base.users[0].id

    async with async_session_maker() as session, session.begin():
        await session.execute(delete(Tenant).where(Tenant.id == tenant.id))
    assert await _usage_rows(tenant.id) == []
