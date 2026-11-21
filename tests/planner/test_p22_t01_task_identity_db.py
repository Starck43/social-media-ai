"""Canonical task access and occurrence behavior on an isolated PostgreSQL schema."""

from contextlib import contextmanager
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.core.access_catalog import workspace_grants
from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import Contact, PlatformRole, Tenant, TenantUser, User, WorkspaceRole
from app.services.planner.canonical_tasks import CreateTask, UpdateParticipation, UpdateTask, task_service
from app.services.planner.tasks import PlannerConflict, PlannerUnavailable, planner_service
from tests.support.schema_guard import require_isolated_schema

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


@pytest.fixture
async def task_world():
    tag = f"p22-{uuid4().hex}"
    async with async_session_maker() as session:
        platform_role = await session.scalar(select(PlatformRole.id).where(PlatformRole.codename == "user"))
        role = await session.scalar(select(WorkspaceRole.id).where(WorkspaceRole.codename == "owner"))
        users = [
            User(
                username=f"{tag}-{i}",
                email=f"{tag}-{i}@example.invalid",
                hashed_password="synthetic",
                role_id=platform_role,
                is_active=True,
            )
            for i in range(3)
        ]
        session.add_all(users)
        await session.flush()
        tenants = [Tenant(name=f"{tag}-{i}", slug=f"{tag}-{i}", plan_holder_user_id=users[2 * i].id) for i in range(2)]
        session.add_all(tenants)
        await session.flush()
        session.add_all(
            [
                TenantUser(
                    tenant_id=tenants[ti].id,
                    user_id=users[ui].id,
                    channel="web",
                    external_user_id=str(users[ui].id),
                    role_id=role,
                )
                for ui, ti in ((0, 0), (1, 0), (2, 1))
            ]
        )
        await session.commit()
        try:
            yield SimpleNamespace(tenants=tenants, users=users)
        finally:
            for row in tenants:
                await session.delete(row)
            await session.flush()
            for row in users:
                await session.delete(row)
            await session.commit()


@contextmanager
def actor(world, user_idx, workspace=0, *, role="owner"):
    with (
        tenant_scope(world.tenants[workspace].id),
        permission_scope(world.users[user_idx], member_grants=workspace_grants(role)),
    ):
        yield


async def test_private_shared_task_and_recurring_completion(task_world, monkeypatch):
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    world = task_world
    with actor(world, 0):
        note = await task_service.create(CreateTask(title="Review the synthetic plan"))
        assert (await planner_service.list())["items"] == []
        with pytest.raises(PermissionError):
            await task_service.set_participant(
                note.id,
                UpdateParticipation(
                    expected_version=note.version, user_id=world.users[1].id, role="watcher", enabled=True
                ),
            )
    with actor(world, 1):
        with pytest.raises(PlannerUnavailable):
            await task_service.get(note.id)
    with actor(world, 2, workspace=1):
        with pytest.raises(PlannerUnavailable):
            await task_service.get(note.id)

    with actor(world, 0):
        recurring = await task_service.create(
            CreateTask(
                title="Review synthetic recurring work",
                visibility="shared",
                due_date=date(2099, 11, 21),
                recurrence="FREQ=DAILY",
            )
        )
        changed = await task_service.set_participant(
            recurring.id,
            UpdateParticipation(
                expected_version=recurring.version, user_id=world.users[1].id, role="collaborator", enabled=True
            ),
        )
        with pytest.raises(PlannerConflict):
            await task_service.update(
                recurring.id, UpdateTask(expected_version=recurring.version, scope="occurrence", title="stale")
            )
        async with async_session_maker() as session, session.begin():
            with pytest.raises(PlannerConflict, match="planner_parent_cycle"):
                await task_service.update(
                    recurring.id,
                    UpdateTask(
                        expected_version=changed.version,
                        scope="occurrence",
                        visibility="private",
                        parent_id=recurring.id,
                    ),
                    session=session,
                )
    with actor(world, 1):
        assert (await task_service.list(view="mine"))["items"] == [changed]
        first = await task_service.complete(recurring.id, changed.version)
        replay = await task_service.complete(recurring.id, changed.version)
        assert first == replay
        assert first.next_task.due_date == date(2099, 11, 22)
    with actor(world, 0):
        private = await task_service.update(
            first.next_task.id,
            UpdateTask(expected_version=first.next_task.version, scope="occurrence", visibility="private"),
        )
    with actor(world, 1):
        with pytest.raises(PlannerUnavailable):
            await task_service.get(private.id)


async def test_recurring_task_drops_stale_assignee_and_merged_contact(task_world, monkeypatch):
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    world = task_world
    tenant_id = world.tenants[0].id
    assignee_id = world.users[1].id
    async with async_session_maker() as session:
        source = Contact(tenant_id=tenant_id, display_name_encrypted="Synthetic contact")
        target = Contact(tenant_id=tenant_id, display_name_encrypted="Synthetic merge target")
        session.add_all([source, target])
        await session.commit()

    with actor(world, 0):
        task = await task_service.create(
            CreateTask(
                title="Review synthetic recurring references",
                assignee_id=assignee_id,
                contact_id=source.id,
                due_date=date(2099, 11, 21),
                recurrence="FREQ=DAILY",
            )
        )

    async with async_session_maker() as session:
        membership = await session.scalar(
            select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.user_id == assignee_id)
        )
        membership.is_active = False
        contact = await session.get(Contact, source.id)
        contact.merged_into_id = target.id
        await session.commit()

    with actor(world, 0):
        completion = await task_service.complete(task.id, task.version)
        assert completion.task.status == "done"
        assert completion.task.assignee_id == assignee_id
        assert completion.task.contact_id == source.id
        assert completion.next_task.assignee_id is None
        assert completion.next_task.contact_id is None
        following = await task_service.complete(completion.next_task.id, completion.next_task.version)
        assert following.next_task.assignee_id is None
        assert following.next_task.contact_id is None


async def test_assignee_can_edit_and_complete_without_seeing_restricted_links(task_world, monkeypatch):
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    world = task_world
    tenant_id, assignee_id = world.tenants[0].id, world.users[1].id
    async with async_session_maker() as session:
        analyst_role = await session.scalar(select(WorkspaceRole.id).where(WorkspaceRole.codename == "analyst"))
        membership = await session.scalar(
            select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.user_id == assignee_id)
        )
        membership.role_id = analyst_role
        contact = Contact(tenant_id=tenant_id, display_name_encrypted="Restricted synthetic contact")
        session.add(contact)
        await session.commit()

    with actor(world, 0):
        parent = await task_service.create(CreateTask(title="Private parent"))
        child = await task_service.create(
            CreateTask(
                title="Shared child",
                parent_id=parent.id,
                contact_id=contact.id,
                visibility="shared",
                assignee_id=assignee_id,
                due_at=datetime(2099, 3, 28, 9, tzinfo=ZoneInfo("Europe/Berlin")),
                timezone="Europe/Berlin",
                recurrence="FREQ=DAILY",
            )
        )

    with actor(world, 1, role="analyst"):
        restricted = await task_service.get(child.id)
        assert restricted.parent_id is None and restricted.contact_id is None
        assert (await task_service.list(view="team"))["items"] == [restricted]
        with pytest.raises(PermissionError, match="planner_contact_denied"):
            await task_service.update(
                child.id, UpdateTask(expected_version=child.version, scope="series", contact_id=contact.id)
            )
        changed = await task_service.update(
            child.id, UpdateTask(expected_version=child.version, scope="series", title="Edited shared child")
        )
        assert changed.parent_id is None and changed.contact_id is None
        completed = await task_service.complete(child.id, changed.version)
        assert completed.task.status == "done"
        assert completed.next_task.title == "Edited shared child"
        assert completed.next_task.due_at == datetime(2099, 3, 29, 7, tzinfo=timezone.utc)
        assert completed.next_task.parent_id is None and completed.next_task.contact_id is None

    with actor(world, 0):
        following = await task_service.get(completed.next_task.id)
        assert following.parent_id == parent.id and following.contact_id == contact.id
