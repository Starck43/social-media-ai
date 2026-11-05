"""Post-integration PostgreSQL handoff; never run through the remote no-DB route.

Requires the real catalog/bootstrap wiring and authorized 0140 on test_schema.
Root conftest validates without bootstrapping. Fixtures own and verify cleanup
of synthetic IDs only; no reference rows, schema or migration changes here.
"""

import asyncio
from contextlib import ExitStack
from datetime import date

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.core.access_catalog import resolve_permission_key, workspace_grants
from app.core.config import settings
from app.core.database import async_engine, async_session_maker
from app.core.permissions import operator_permission_scope, permission_scope
from app.core.tenant_context import tenant_scope
from app.models import Contact, Tenant, TenantUser, User
from app.models.planner import PlannerTask
from app.services.planner.contracts import TaskCreate, TaskReschedule, TaskUpdate
from app.services.planner.tasks import PlannerConflict, PlannerUnavailable, planner_service
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]
ANCHOR = date(2099, 1, 31)


@pytest.fixture(autouse=True)
def planner_integration_gate(monkeypatch):
    assert settings.DB_SCHEMA == "test_schema", "planner integration is test_schema only"
    assert resolve_permission_key("planner.task.create") is not None, "post-#391 catalog wiring required"
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())


@pytest.fixture
async def planner_world(personal_world):
    world = personal_world
    tenant_ids = [row.id for row in world.tenants]
    user_ids = [row.id for row in world.users]
    try:
        yield world
    finally:
        with tenant_scope(bypass=True), operator_permission_scope():
            for tenant_id in tenant_ids:
                await Tenant.objects.delete_by_id(tenant_id)
            for user_id in user_ids:
                await User.objects.delete_by_id(user_id)
            async with async_session_maker() as session:
                assert (
                    await session.scalar(
                        select(func.count()).select_from(PlannerTask).where(PlannerTask.tenant_id.in_(tenant_ids))
                    )
                    == 0
                )
                assert (
                    await session.scalar(select(func.count()).select_from(Tenant).where(Tenant.id.in_(tenant_ids))) == 0
                )
                assert await session.scalar(select(func.count()).select_from(User).where(User.id.in_(user_ids))) == 0


def scope(world, workspace=0, user=None):
    stack = ExitStack()
    stack.enter_context(tenant_scope(world.tenants[workspace].id))
    stack.enter_context(
        permission_scope(
            world.users[user if user is not None else (0 if workspace == 0 else 2)],
            member_grants=workspace_grants("owner"),
        )
    )
    return stack


async def test_tenant_crud_subtasks_versions_and_ciphertext(planner_world):
    world = planner_world
    with scope(world):
        parent = await planner_service.create(TaskCreate(title="Synthetic planner root"))
        child = await planner_service.create(
            TaskCreate(
                title="Synthetic private planner child",
                parent_id=parent.id,
                due_date=ANCHOR,
                priority="critical",
                tags=["synthetic"],
            )
        )
        assert child.parent_id == parent.id and child.priority == "critical"
        assert {item.id for item in (await planner_service.list())["items"]} == {parent.id, child.id}
        changed = await planner_service.update(
            child.id, TaskUpdate(expected_version=1, scope="occurrence", title="Renamed")
        )
        assert changed.version == 2
        with pytest.raises(PlannerConflict):
            await planner_service.update(child.id, TaskUpdate(expected_version=1, scope="occurrence", title="stale"))
        with pytest.raises(PlannerConflict, match="cycle"):
            await planner_service.update(
                parent.id, TaskUpdate(expected_version=1, scope="occurrence", parent_id=child.id)
            )
        schema = async_engine.dialect.identifier_preparer.quote_schema(settings.DB_SCHEMA)
        async with async_engine.connect() as connection:
            stored = (
                await connection.execute(
                    text(
                        f"SELECT title_encrypted, description_encrypted, series_template_encrypted FROM {schema}.planner_tasks WHERE id=:id"
                    ),
                    {"id": child.id},
                )
            ).one()
            assert all(value.startswith("pii:v1:") for value in stored)
            assert "Synthetic" not in repr(stored) and "Renamed" not in repr(stored)
    with scope(world, 1):
        assert not (await planner_service.list())["items"]
        with pytest.raises(PlannerUnavailable):
            await planner_service.get(child.id)
        with pytest.raises(PlannerUnavailable):
            await planner_service.create(TaskCreate(title="Foreign parent", parent_id=parent.id))
        with pytest.raises(PlannerUnavailable):
            await planner_service.update(child.id, TaskUpdate(expected_version=2, scope="occurrence", title="foreign"))


async def test_concurrent_completion_and_retry_have_one_successor(planner_world):
    with scope(planner_world):
        first = await planner_service.create(
            TaskCreate(title="Concurrent recurrence", due_date=ANCHOR, recurrence="FREQ=MONTHLY;COUNT=3")
        )
        left, right = await asyncio.gather(planner_service.complete(first.id, 1), planner_service.complete(first.id, 1))
        assert left.next_task.id == right.next_task.id
        assert left.next_task.due_date == date(2099, 3, 31)
        replay = await planner_service.complete(first.id, 1)
        assert replay.next_task.id == left.next_task.id
        page = await planner_service.list()
        assert len(page["items"]) == 2
        with pytest.raises(PlannerConflict):
            await planner_service.update(first.id, TaskUpdate(expected_version=2, scope="series", title="closed"))


async def test_occurrence_edit_does_not_rewrite_series_and_external_source_stops(planner_world):
    world = planner_world
    with scope(world):
        first = await planner_service.create(
            TaskCreate(title="Series default", due_date=ANCHOR, recurrence="FREQ=DAILY;COUNT=3")
        )
        changed = await planner_service.update(
            first.id, TaskUpdate(expected_version=1, scope="occurrence", title="Only once")
        )
        moved = await planner_service.reschedule(
            first.id,
            TaskReschedule(
                expected_version=changed.version, scope="occurrence", due_date=date(2099, 2, 1), timezone="UTC"
            ),
        )
        result = await planner_service.complete(first.id, moved.version)
        assert result.next_task.title == "Series default" and result.next_task.due_date == date(2099, 2, 2)
        async with async_session_maker() as session:
            following = await session.get(PlannerTask, result.next_task.id)
            following.recurrence_source = "calendar:synthetic"
            await session.commit()
        with pytest.raises(PlannerConflict, match="source_unavailable"):
            await planner_service.complete(result.next_task.id, 1)
        assert len((await planner_service.list())["items"]) == 2


async def test_composite_parent_fk_and_contact_erasure(planner_world):
    world = planner_world
    with scope(world):
        contact = await Contact.objects.create(display_name="Synthetic planner link")
        first = await planner_service.create(TaskCreate(title="Linked", contact_id=contact.id))
        await Contact.objects.delete_by_id(contact.id)
        assert (await planner_service.get(first.id)).contact_id is None
    with scope(world, 1):
        foreign = await planner_service.create(TaskCreate(title="Other workspace"))
        with pytest.raises(IntegrityError):
            async with async_session_maker() as session:
                item = await session.get(PlannerTask, foreign.id)
                item.parent_id = first.id
                await session.commit()
        assert (await planner_service.get(foreign.id)).parent_id is None


async def test_delete_cascades_suffix_subtasks_and_root(planner_world):
    with scope(planner_world):
        first = await planner_service.create(
            TaskCreate(title="Delete series", due_date=ANCHOR, recurrence="FREQ=DAILY;COUNT=4")
        )
        second = (await planner_service.complete(first.id, 1)).next_task
        third = (await planner_service.complete(second.id, 1)).next_task
        child = await planner_service.create(TaskCreate(title="Owned subtask", parent_id=second.id))
        await planner_service.delete(second.id, 2)
        assert [item.id for item in (await planner_service.list())["items"]] == [first.id]
        for task_id in (second.id, third.id, child.id):
            with pytest.raises(PlannerUnavailable):
                await planner_service.get(task_id)
        assert (await planner_service.complete(first.id, 1)).next_task is None
        await planner_service.delete(first.id, 2)
        assert not (await planner_service.list())["items"]


async def test_membership_revocation_is_rechecked_not_cached(planner_world):
    world = planner_world
    with scope(world):
        first = await planner_service.create(TaskCreate(title="Revocation"))
    with tenant_scope(bypass=True), operator_permission_scope():
        await TenantUser.objects.update_by_id(world.memberships[0].id, is_active=False)
    with scope(world):
        with pytest.raises(PermissionError):
            await planner_service.get(first.id)
        with pytest.raises(PermissionError):
            await planner_service.create(TaskCreate(title="Must be denied"))
