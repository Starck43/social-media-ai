"""Local-only PostgreSQL handoff: 0145 on isolated, exclusively owned test_schema.

Do not run via the remote no-DB route. The personal_world fixture deletes its
synthetic tenants/users; runner must inventory before/after and verify cleanup.
"""

import asyncio
from contextlib import ExitStack
from datetime import date

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.core.access_catalog import workspace_grants
from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import operator_permission_scope, permission_scope
from app.core.tenant_context import tenant_scope
from app.models import Contact, TenantUser
from app.models.planner import PlannerEventRun, PlannerEventSettings, PlannerTask
from app.services.planner.events import event_planning_service
from app.services.planner.tasks import planner_service
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]
TODAY = date(2032, 2, 26)


@pytest.fixture(autouse=True)
def isolated_schema(monkeypatch):
    assert settings.DB_SCHEMA == settings.DB_TEST_SCHEMA and settings.DB_SCHEMA.startswith(
        "test_schema"
    ), "P16-T05 DB checks require the configured test_schema* schema"
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())


def owner_scope(world):
    stack = ExitStack()
    stack.enter_context(tenant_scope(world.tenants[0].id))
    stack.enter_context(permission_scope(world.users[0], member_grants=workspace_grants("owner")))
    return stack


async def configure(world, enabled=True):
    with owner_scope(world):
        async with async_session_maker() as session, session.begin():
            await event_planning_service.set_enabled(enabled, session=session)


async def fire(world):
    with tenant_scope(world.tenants[0].id):
        async with async_session_maker() as session, session.begin():
            return await event_planning_service.run_daily(session=session, reference_date=TODAY)


async def test_daily_concurrent_retries_deletion_and_idempotency(personal_world):
    world = personal_world
    with owner_scope(world):
        contact = await Contact.objects.create(display_name="Synthetic event", birthday="03-04")
    assert (await fire(world))["status"] == "disabled"
    await configure(world)
    first, second = await asyncio.gather(fire(world), fire(world))
    assert sorted((first["created"], second["created"])) == [0, 1]
    assert (await fire(world))["created"] == 0
    with owner_scope(world):
        async with async_session_maker() as session:
            run = await session.scalar(select(PlannerEventRun).where(PlannerEventRun.contact_id == contact.id))
            assert run and run.year == 2032 and run.greeting_task_id and run.root_task_id
            root_id, greeting_id = run.root_task_id, run.greeting_task_id
            tasks = (await session.scalars(select(PlannerTask).where(PlannerTask.contact_id == contact.id))).all()
            assert len(tasks) == 3
        await planner_service.delete(root_id, 1)
        async with async_session_maker() as session:
            run = await session.scalar(select(PlannerEventRun).where(PlannerEventRun.contact_id == contact.id))
            assert run.root_task_id is None and run.greeting_task_id is None
        assert (await fire(world))["created"] == 0
        assert greeting_id != root_id
        await Contact.objects.delete_by_id(contact.id)
        async with async_session_maker() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(PlannerEventRun).where(PlannerEventRun.contact_id == contact.id)
                )
                == 0
            )


async def test_revoked_enabler_stops_without_transfer(personal_world):
    world = personal_world
    with owner_scope(world):
        await Contact.objects.create(display_name="Synthetic revoked", birthday="03-04")
    await configure(world)
    with tenant_scope(bypass=True), operator_permission_scope():
        async with async_session_maker() as session, session.begin():
            member = await session.get(TenantUser, world.memberships[0].id)
            member.is_active = False
    assert await fire(world) == {"status": "owner_unavailable", "created": 0}
    with tenant_scope(bypass=True), operator_permission_scope():
        async with async_session_maker() as session:
            config = await session.get(PlannerEventSettings, world.tenants[0].id)
            assert config.owner_user_id == world.users[0].id
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(PlannerEventRun)
                    .where(PlannerEventRun.tenant_id == world.tenants[0].id)
                )
                == 0
            )


async def test_failure_rolls_back_ledger_and_all_tasks(personal_world, monkeypatch):
    world = personal_world
    with owner_scope(world):
        contact = await Contact.objects.create(display_name="Synthetic rollback", birthday="03-04")
    await configure(world)
    original = planner_service.create
    calls = 0

    async def fail_child(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic child failure")
        return await original(*args, **kwargs)

    monkeypatch.setattr(planner_service, "create", fail_child)
    with pytest.raises(RuntimeError, match="synthetic child failure"):
        await fire(world)
    async with async_session_maker() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(PlannerEventRun).where(PlannerEventRun.contact_id == contact.id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(PlannerTask).where(PlannerTask.contact_id == contact.id)
            )
            == 0
        )


async def test_contact_erasure_keeps_generated_tasks_with_null_link(personal_world):
    world = personal_world
    with owner_scope(world):
        contact = await Contact.objects.create(display_name="Synthetic privacy event", birthday="03-04")
    await configure(world)
    assert (await fire(world))["created"] == 1
    with owner_scope(world):
        async with async_session_maker() as session:
            ids = list(
                (await session.scalars(select(PlannerTask.id).where(PlannerTask.contact_id == contact.id))).all()
            )
            assert len(ids) == 3
        await Contact.objects.delete_by_id(contact.id)
        async with async_session_maker() as session:
            rows = list((await session.scalars(select(PlannerTask).where(PlannerTask.id.in_(ids)))).all())
            assert len(rows) == 3 and all(row.contact_id is None for row in rows)
            assert (
                await session.scalar(
                    select(func.count()).select_from(PlannerEventRun).where(PlannerEventRun.contact_id == contact.id)
                )
                == 0
            )
