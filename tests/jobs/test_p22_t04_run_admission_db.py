"""Canonical run admission on a task-owned PostgreSQL schema, with fake mail only."""

import asyncio
import json

import pytest
from sqlalchemy import select

from app.core.database import async_session_maker
from app.core.tenant_context import tenant_scope
from app.models.planner.task_run import TaskRun, TaskTrigger
from app.services.planner.canonical_tasks import CreateTask, task_service
from app.services.task_execution_plans import execution_plan_service
from app.services.task_run_admission import TaskRunAdmissionError, task_run_service
from app.services.task_run_triggers import task_trigger_service
from tests.jobs.test_p22_t03_execution_plan_db import _connection
from tests.shared.execution_plan_payloads import mail_plan
from tests.support.schema_guard import require_isolated_schema

pytest_plugins = ("tests.jobs.test_p22_t03_execution_plan_db",)
pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


async def _publish(workspace, *, scheduled=False):
    connection_id, pin = await _connection(workspace)
    plan = mail_plan(connection_id, pin)
    plan["steps"] = plan["steps"][:2]  # Only the supported bounded read operations.
    if scheduled:
        plan["trigger"] = {"type": "schedule", "cron": "* * * * *", "timezone": "UTC"}
    with workspace.scope():
        task = await task_service.create(CreateTask(title="Synthetic run task"))
        reference = await execution_plan_service.publish(task.id, task.version, json.dumps(plan))
    return task, reference, connection_id


async def test_duplicate_manual_requests_are_atomic_and_conflicts_fail(workspace):
    require_isolated_schema()
    task, reference, _ = await _publish(workspace)
    with workspace.scope():
        receipts = await asyncio.gather(
            *(
                task_run_service.admit_manual(task.id, expected_revision=reference.revision, request_key="request-1")
                for _ in range(2)
            )
        )
        assert len({receipt.run_id for receipt in receipts}) == 1
        assert len({receipt.job_id for receipt in receipts}) == 1
        assert sorted(receipt.replayed for receipt in receipts) == [False, True]
        with pytest.raises(TaskRunAdmissionError, match="request_key_conflict"):
            await task_run_service.admit_manual(
                task.id, expected_revision=reference.revision + 1, request_key="request-1"
            )
    async with async_session_maker() as session:
        runs = (await session.scalars(select(TaskRun).where(TaskRun.tenant_id == workspace.tenants[0].id))).all()
        assert len(runs) == 1


async def test_manual_run_preserves_cursor_and_scheduled_overlap_is_durable_skip(workspace):
    require_isolated_schema()
    task, reference, _ = await _publish(workspace, scheduled=True)
    with workspace.scope():
        trigger = await task_trigger_service.configure(
            task.id, revision=reference.revision, expected_version=reference.task_version, enabled=True
        )
        manual = await task_run_service.admit_manual(
            task.id, expected_revision=reference.revision, request_key="manual"
        )
        async with async_session_maker() as session:
            before = await session.scalar(select(TaskTrigger).where(TaskTrigger.id == trigger.trigger_id))
            assert before.next_due_at == trigger.next_due_at
    with tenant_scope(workspace.tenants[0].id):
        stats = await task_trigger_service.tick(workspace.tenants[0].id, trigger.next_due_at)
    assert stats == {"due": 1, "enqueued": 0, "failed": 0}
    async with async_session_maker() as session:
        runs = (await session.scalars(select(TaskRun).where(TaskRun.task_id == task.id).order_by(TaskRun.id))).all()
        current = await session.scalar(select(TaskTrigger).where(TaskTrigger.id == trigger.trigger_id))
        assert len(runs) == 2 and runs[0].id == manual.run_id
        assert runs[1].status == "skipped" and runs[1].job_id is None
        assert runs[1].result == {"reason": "active_run"}
        assert current.next_due_at > trigger.next_due_at
