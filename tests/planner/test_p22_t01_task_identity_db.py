"""Canonical task access and occurrence behavior on an isolated PostgreSQL schema."""

from contextlib import contextmanager
from datetime import date

import pytest
from cryptography.fernet import Fernet

from app.core.access_catalog import workspace_grants
from app.core.config import settings
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.services.planner.canonical_tasks import CreateTask, UpdateParticipation, UpdateTask, task_service
from app.services.planner.tasks import PlannerConflict, PlannerUnavailable, planner_service
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401
from tests.support.schema_guard import require_isolated_schema

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


@contextmanager
def actor(world, user_idx, workspace=0):
    with (
        tenant_scope(world.tenants[workspace].id),
        permission_scope(world.users[user_idx], member_grants=workspace_grants("owner")),
    ):
        yield


async def test_private_shared_task_and_recurring_completion(personal_world, monkeypatch):
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    world = personal_world
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
