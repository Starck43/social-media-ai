"""Task deletion confirmation, recurrence and queue fencing on PostgreSQL."""

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.models import Job
from app.models.managers.job_manager import JobManager
from app.models.planner import Task, TaskPlanRevision, TaskRun, TaskTrigger
from app.services.commands.task_deletion import task_deletion_service
from app.services.planner.canonical_tasks import CreateTask, UpdateTask, task_service
from app.services.planner.tasks import PlannerConflict, PlannerUnavailable
from app.services.task_run_admission import CANONICAL_JOB_TYPE
from app.services.workspace.task_surface import canonical_task_actions
from app.types import ActionType
from tests.planner.test_p22_t01_task_identity_db import actor, task_world  # noqa: F401
from tests.support.schema_guard import require_isolated_schema

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


async def test_occurrence_delete_and_series_confirmation(task_world, monkeypatch):  # noqa: F811
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    with actor(task_world, 0):
        task = await task_service.create(
            CreateTask(title="Recurring review", due_date=date(2020, 1, 1), recurrence="FREQ=DAILY")
        )
        preview = await task_deletion_service.preview(task.id, scope="occurrence")
        assert preview.effects.next_occurrence is not None
        await task_service.update(
            task.id, UpdateTask(expected_version=task.version, scope="occurrence", title="Edited review")
        )
        with pytest.raises(PlannerConflict, match="preview_changed"):
            await task_deletion_service.confirm(task.id, scope="occurrence", confirmation=preview.confirmation)
        preview = await task_deletion_service.preview(task.id, scope="occurrence")
        with pytest.raises(PlannerConflict, match="preview_changed"):
            await task_deletion_service.confirm(task.id, scope="series", confirmation=preview.confirmation)
        await task_deletion_service.confirm(task.id, scope="occurrence", confirmation=preview.confirmation)
        with pytest.raises(PlannerUnavailable):
            await task_service.get(task.id)
        (successor,) = (await task_service.list())["items"]
        preview = await task_deletion_service.preview(successor.id, scope="series")
        assert preview.effects.occurrences == 1
        assert preview.effects.next_occurrence is None
        await task_deletion_service.confirm(successor.id, scope="series", confirmation=preview.confirmation)
        assert (await task_service.list())["items"] == []
    async with async_session_maker() as session:
        assert (await session.get(Task, task.id)).deleted_at is not None
        assert (await session.get(Task, successor.id)).previous_occurrence_id == task.id


async def _seed_run(task, world):
    now = datetime.now(timezone.utc)
    tenant_id, actor_id = world.tenants[0].id, world.users[0].id
    async with async_session_maker() as session, session.begin():
        session.add(TaskPlanRevision(tenant_id=tenant_id, task_id=task.id, revision=1, definition_encrypted="fixture"))
        await session.flush()
        trigger = TaskTrigger(
            tenant_id=tenant_id,
            task_id=task.id,
            revision=1,
            principal_id=actor_id,
            cron_expression="* * * * *",
            timezone_name="UTC",
            enabled=True,
            next_due_at=now,
        )
        run = TaskRun(
            tenant_id=tenant_id,
            task_id=task.id,
            revision=1,
            manual_actor_id=actor_id,
            principal_id=actor_id,
            request_key=uuid4().hex,
            fingerprint="fixture",
            effective_at=now,
            status="pending",
            admitted_inputs_encrypted="fixture",
        )
        session.add_all([trigger, run])
        await session.flush()
        job = Job(tenant_id=tenant_id, task_run_id=run.id, job_type=CANONICAL_JOB_TYPE, run_at=now)
        session.add(job)
        await session.flush()
        run.job_id = job.id
        return trigger.id, run.id, job.id


@pytest.mark.parametrize("claimed", [False, True])
async def test_delete_fences_queue_and_keeps_receipts(task_world, monkeypatch, claimed):  # noqa: F811
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    with actor(task_world, 0):
        task = await task_service.create(CreateTask(title="Synthetic queue action"))
        trigger_id, run_id, job_id = await _seed_run(task, task_world)
        detail = await canonical_task_actions(task.id)
        assert detail["latest_run"] == {"run_id": run_id, "status": "pending"}
        assert "delete" in {action["reference"] for action in detail["actions"]}
        preview = await task_deletion_service.preview(task.id, scope="occurrence")
        assert preview.effects.enabled_schedules == preview.effects.queued_jobs == 1
        assert preview.effects.retained_run_receipts == 1
        async with async_session_maker() as session, session.begin():
            await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
            with pytest.raises(PlannerConflict, match="worker_busy"):
                await task_deletion_service.confirm(task.id, scope="occurrence", confirmation=preview.confirmation)
        with permission_scope(task_world.users[0], member_grants={("plannertask", ActionType.VIEW)}):
            restricted = await canonical_task_actions(task.id)
            assert restricted["actions"] == []
            with pytest.raises(PermissionError):
                await task_deletion_service.confirm(task.id, scope="occurrence", confirmation=preview.confirmation)
        if claimed:
            assert await JobManager.claim_job(job_id) is not None
            with pytest.raises(PlannerConflict, match="preview_changed"):
                await task_deletion_service.confirm(task.id, scope="occurrence", confirmation=preview.confirmation)
            preview = await task_deletion_service.preview(task.id, scope="occurrence")
            assert preview.effects.running_jobs == 1
        outcome = await task_deletion_service.confirm(task.id, scope="occurrence", confirmation=preview.confirmation)
        assert outcome.in_flight_uncertain == int(claimed)
        assert await JobManager.claim_job(job_id) is None
    async with async_session_maker() as session:
        run = await session.get(TaskRun, run_id)
        assert run.status == ("unknown" if claimed else "cancelled")
        assert (await session.get(Job, job_id)).status == "failed"
        trigger = await session.get(TaskTrigger, trigger_id)
        assert not trigger.enabled and trigger.next_due_at is None
