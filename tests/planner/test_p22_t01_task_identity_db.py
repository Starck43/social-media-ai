"""Canonical task access and occurrence behavior on an isolated PostgreSQL schema."""

from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.core.access_catalog import workspace_grants
from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import PlatformRole, Tenant, TenantUser, User, WorkspaceRole
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
            for row in (*tenants, *users):
                await session.delete(row)
            await session.commit()


@contextmanager
def actor(world, user_idx, workspace=0):
    with (
        tenant_scope(world.tenants[workspace].id),
        permission_scope(world.users[user_idx], member_grants=workspace_grants("owner")),
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
