"""Confirmed closure, reopening and schedule lifecycle on PostgreSQL.

Covers the accepted T01 recurrence, T04 admission/scheduler and T05 confirmed
command contracts: close vs scheduler race, stale version, replay, no duplicate
successor or trigger, retained receipts and permission refusal.
"""

import json
from datetime import date

import pytest
from sqlalchemy import select

from app.core.database import async_session_maker
from app.core.tenant_context import tenant_scope
from app.models.planner import Task, TaskRun, TaskTrigger
from app.services.commands.task_closure import task_closure_service
from app.services.planner.canonical_tasks import CreateTask, task_service
from app.services.planner.tasks import PlannerConflict
from app.services.task_execution_plans import execution_plan_service
from app.services.task_run_admission import TaskRunAdmissionError, task_run_service
from app.services.task_run_triggers import task_trigger_service
from app.services.workspace.task_surface import canonical_task_actions
from app.types import ActionType
from tests.jobs.test_p22_t03_execution_plan_db import _connection
from tests.shared.execution_plan_payloads import mail_plan
from tests.support.schema_guard import require_isolated_schema

pytest_plugins = ("tests.jobs.test_p22_t03_execution_plan_db",)
pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


async def _scheduled(workspace, *, scheduled=True):
    connection_id, pin = await _connection(workspace)
    plan = mail_plan(connection_id, pin)
    plan["steps"] = plan["steps"][:2]
    if scheduled:
        plan["trigger"] = {"type": "schedule", "cron": "* * * * *", "timezone": "UTC"}
    with workspace.scope():
        task = await task_service.create(CreateTask(title="Synthetic scheduled work"))
        reference = await execution_plan_service.publish(task.id, task.version, json.dumps(plan))
        trigger = None
        if scheduled:
            trigger = await task_trigger_service.configure(
                task.id, revision=reference.revision, expected_version=reference.task_version, enabled=True
            )
    return task, reference, trigger


async def _row(model, row_id):
    async with async_session_maker() as session:
        return await session.get(model, row_id)


async def test_overdue_close_states_its_effects_and_refuses_stale_or_replayed_tokens(workspace):
    require_isolated_schema()
    with workspace.scope():
        task = await task_service.create(CreateTask(title="Overdue synthetic note", due_date=date(2020, 1, 1)))
        preview = await task_closure_service.preview(task.id, operation="close")
        assert preview.effects.status == "todo" and preview.effects.target_status == "done"
        assert preview.effects.scope == "occurrence" and preview.effects.schedules == 0
        assert preview.effects.creates_occurrence is False and preview.effects.successor_task_id is None
        assert preview.expires_in > 0

        # A change after the preview invalidates the exact version it described.
        from app.services.planner.canonical_tasks import UpdateTask

        await task_service.update(
            task.id, UpdateTask(expected_version=task.version, scope="occurrence", title="Renamed note")
        )
        with pytest.raises(PlannerConflict, match="preview_changed"):
            await task_closure_service.confirm(task.id, operation="close", confirmation=preview.confirmation)

        preview = await task_closure_service.preview(task.id, operation="close")
        outcome = await task_closure_service.confirm(task.id, operation="close", confirmation=preview.confirmation)
        assert outcome.replayed is False and outcome.effects.status == "done"
        assert outcome.effects.retained_run_receipts == 0

        # A repeated confirmation converges instead of closing twice.
        replay = await task_closure_service.confirm(task.id, operation="close", confirmation=preview.confirmation)
        assert replay.replayed is True and replay.effects.status == "done"
        with pytest.raises(PlannerConflict, match="task_already_closed"):
            await task_closure_service.preview(task.id, operation="close")
    row = await _row(Task, task.id)
    assert row.status == "done" and row.completed_at is not None and row.version == task.version + 2


async def test_recurring_close_keeps_successor_semantics_and_never_duplicates_it(workspace):
    require_isolated_schema()
    with workspace.scope():
        task = await task_service.create(
            CreateTask(title="Recurring review", due_date=date(2020, 1, 1), recurrence="FREQ=DAILY")
        )
        preview = await task_closure_service.preview(task.id, operation="close")
        assert preview.effects.creates_occurrence is True and preview.effects.successor_due is not None
        assert preview.effects.successor_task_id is None
        await task_closure_service.confirm(task.id, operation="close", confirmation=preview.confirmation)

        reopen = await task_closure_service.preview(task.id, operation="reopen")
        assert reopen.effects.status == "done" and reopen.effects.target_status == "todo"
        successor_id = reopen.effects.successor_task_id
        assert successor_id is not None and reopen.effects.creates_occurrence is False
        await task_closure_service.confirm(task.id, operation="reopen", confirmation=reopen.confirmation)
        assert (await task_service.get(task.id)).status == "todo"

        # Re-closing the reopened occurrence reuses the admitted successor.
        again = await task_closure_service.preview(task.id, operation="close")
        assert again.effects.creates_occurrence is False and again.effects.successor_task_id == successor_id
        await task_closure_service.confirm(task.id, operation="close", confirmation=again.confirmation)
    async with async_session_maker() as session:
        series = (
            await session.scalars(
                select(Task).where(Task.tenant_id == workspace.tenants[0].id, Task.series_id == task.series_id)
            )
        ).all()
    assert len(series) == 2
    assert (await _row(Task, successor_id)).previous_occurrence_id == task.id


async def test_scheduled_close_disables_the_trigger_and_stops_admission(workspace):
    require_isolated_schema()
    task, reference, trigger = await _scheduled(workspace)
    with workspace.scope():
        preview = await task_closure_service.preview(task.id, operation="close")
        assert preview.effects.schedules == 1 and preview.effects.enabled_schedules == 1
        assert preview.effects.creates_occurrence is False
        assert preview.effects.next_due_at == trigger.next_due_at
        outcome = await task_closure_service.confirm(task.id, operation="close", confirmation=preview.confirmation)
        assert outcome.effects.enabled_schedules == 0 and outcome.effects.next_due_at is None
        # The manual run stays a separate command, but closed work admits nothing.
        with pytest.raises(TaskRunAdmissionError, match="task_closed"):
            await task_run_service.admit_manual(task.id, expected_revision=reference.revision, request_key="after")
        references = {action["reference"] for action in (await canonical_task_actions(task.id))["actions"]}
        assert "run" not in references and {"reopen"} <= references
    with tenant_scope(workspace.tenants[0].id):
        stats = await task_trigger_service.tick(workspace.tenants[0].id, trigger.next_due_at)
    assert stats == {"due": 0, "enqueued": 0, "failed": 0}
    trigger_row = await _row(TaskTrigger, trigger.trigger_id)
    assert trigger_row.enabled is False and trigger_row.next_due_at is None


async def test_close_after_an_admitted_occurrence_reports_it_and_keeps_every_receipt(workspace):
    require_isolated_schema()
    task, reference, trigger = await _scheduled(workspace)
    with workspace.scope():
        preview = await task_closure_service.preview(task.id, operation="close")
    # The scheduler wins the race between preview and confirmation.
    with tenant_scope(workspace.tenants[0].id):
        stats = await task_trigger_service.tick(workspace.tenants[0].id, trigger.next_due_at)
    assert stats["enqueued"] == 1
    with workspace.scope():
        with pytest.raises(PlannerConflict, match="preview_changed"):
            await task_closure_service.confirm(task.id, operation="close", confirmation=preview.confirmation)
        preview = await task_closure_service.preview(task.id, operation="close")
        assert preview.effects.admitted_run_id is not None
        assert preview.effects.admitted_run_status == "pending"
        outcome = await task_closure_service.confirm(task.id, operation="close", confirmation=preview.confirmation)
        assert outcome.effects.admitted_run_id == preview.effects.admitted_run_id
        assert outcome.effects.enabled_schedules == 0 and outcome.effects.retained_run_receipts == 1
    # The admitted run is reported, not cancelled, and no further occurrence is due.
    async with async_session_maker() as session:
        runs = (await session.scalars(select(TaskRun).where(TaskRun.task_id == task.id))).all()
        triggers = (await session.scalars(select(TaskTrigger).where(TaskTrigger.task_id == task.id))).all()
    assert len(runs) == 1 and runs[0].status == "pending" and runs[0].job_id is not None
    assert len(triggers) == 1 and triggers[0].enabled is False
    with tenant_scope(workspace.tenants[0].id):
        assert (await task_trigger_service.tick(workspace.tenants[0].id, trigger.next_due_at))["enqueued"] == 0


async def test_reopen_asks_before_reactivating_a_paused_schedule(workspace):
    require_isolated_schema()
    task, reference, trigger = await _scheduled(workspace)
    with workspace.scope():
        close = await task_closure_service.preview(task.id, operation="close")
        await task_closure_service.confirm(task.id, operation="close", confirmation=close.confirmation)

        # The explicit choice is bound to the confirmation, and is refused where
        # no paused schedule exists.
        choice = await task_closure_service.preview(task.id, operation="reopen", reactivate_schedule=True)
        assert choice.effects.reactivates_schedule is True and choice.effects.enabled_schedules == 0
        await task_closure_service.confirm(
            task.id, operation="reopen", reactivate_schedule=True, confirmation=choice.confirmation
        )
        opened = await task_service.get(task.id)
        assert opened.status == "todo"
        rearmed = await _row(TaskTrigger, trigger.trigger_id)
        assert rearmed.enabled is True and rearmed.next_due_at is not None
        assert rearmed.next_due_at >= trigger.next_due_at

        # Without the choice a paused schedule stays paused.
        await task_trigger_service.configure(
            task.id, revision=reference.revision, expected_version=opened.version, enabled=False
        )
        close = await task_closure_service.preview(task.id, operation="close")
        await task_closure_service.confirm(task.id, operation="close", confirmation=close.confirmation)
        plain = await task_closure_service.preview(task.id, operation="reopen")
        assert plain.effects.reactivates_schedule is False
        await task_closure_service.confirm(task.id, operation="reopen", confirmation=plain.confirmation)
        assert (await _row(TaskTrigger, trigger.trigger_id)).enabled is False
        with pytest.raises(PlannerConflict, match="task_already_open"):
            await task_closure_service.preview(task.id, operation="reopen")

        note = await task_service.create(CreateTask(title="Note without schedule"))
        note_close = await task_closure_service.preview(note.id, operation="close")
        await task_closure_service.confirm(note.id, operation="close", confirmation=note_close.confirmation)
        with pytest.raises(PlannerConflict, match="schedule_reactivation_unavailable"):
            await task_closure_service.preview(note.id, operation="reopen", reactivate_schedule=True)


async def test_schedule_pause_keeps_manual_run_and_closure_needs_update_right(workspace):
    require_isolated_schema()
    task, reference, trigger = await _scheduled(workspace)
    with workspace.scope():
        paused = await task_trigger_service.configure(
            task.id, revision=reference.revision, expected_version=trigger.task_version, enabled=False
        )
        assert paused.enabled is False and paused.next_due_at is None
        actions = {action["reference"]: action for action in (await canonical_task_actions(task.id))["actions"]}
        assert actions["schedule"]["enabled"] is False
        assert "run" in actions
        receipt = await task_run_service.admit_manual(
            task.id, expected_revision=reference.revision, request_key="paused-manual"
        )
        assert receipt.status in {"pending", "skipped"}
        armed = await task_trigger_service.configure(
            task.id, revision=reference.revision, expected_version=paused.task_version, enabled=True
        )
        assert armed.enabled is True and armed.next_due_at is not None
    with workspace.scope(grants={("plannertask", ActionType.VIEW)}):
        with pytest.raises(PermissionError):
            await task_closure_service.preview(task.id, operation="close")
        with pytest.raises(PermissionError):
            await task_trigger_service.configure(
                task.id, revision=reference.revision, expected_version=armed.task_version, enabled=False
            )
    async with async_session_maker() as session:
        runs = (await session.scalars(select(TaskRun).where(TaskRun.task_id == task.id))).all()
    assert len(runs) == 1
