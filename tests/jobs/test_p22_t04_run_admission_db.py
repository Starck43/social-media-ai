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


async def test_revoked_mail_access_stops_before_provider_call(workspace, monkeypatch):
    from unittest.mock import MagicMock

    from app.jobs.dispatcher import _execute_claimed
    from app.models.integrations.connection import WorkspaceConnection
    from app.models.managers.job_manager import JobManager
    from app.services import task_run_operations

    require_isolated_schema()
    task, reference, connection_id = await _publish(workspace)
    with workspace.scope():
        receipt = await task_run_service.admit_manual(
            task.id, expected_revision=reference.revision, request_key="revoke"
        )
    async with async_session_maker() as session, session.begin():
        connection = await session.get(WorkspaceConnection, connection_id)
        connection.state = "reauth"
    provider = MagicMock(side_effect=AssertionError("provider must not be reached"))
    monkeypatch.setattr(task_run_operations, "_read_folder", provider)
    job = await JobManager.claim_job(receipt.job_id)
    assert job is not None
    outcome = await _execute_claimed(job)
    provider.assert_not_called()
    assert outcome["status"] == "failed"
    async with async_session_maker() as session:
        run = await session.get(TaskRun, receipt.run_id)
        assert run.status == "failed"
        assert run.result["code"] == "reauth_required"


async def test_retry_scheduled_run_can_be_cancelled(workspace):
    from app.jobs.claim_outcomes import JobClaim, OutcomeAck
    from app.models.managers.job_manager import JobManager

    require_isolated_schema()
    task, reference, _ = await _publish(workspace)
    with workspace.scope():
        receipt = await task_run_service.admit_manual(
            task.id, expected_revision=reference.revision, request_key="retry"
        )
    manager = JobManager()
    job = await manager.claim_job(receipt.job_id)
    assert job is not None
    claim = JobClaim.capture(job)
    with tenant_scope(workspace.tenants[0].id):
        retried = await manager.mark_failed(job.id, "task_step_transaction_rolled_back", allow_retry=True, claim=claim)
        assert retried.acknowledgement == OutcomeAck.RETRY
        assert await manager.cancel_pending(job.id) is True
    async with async_session_maker() as session:
        run = await session.get(TaskRun, receipt.run_id)
        assert run.status == "cancelled" and run.result["code"] == "task_cancelled"
        assert run.checkpoint_encrypted is None


async def test_stale_worker_is_quarantined_without_replay(workspace, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from unittest.mock import MagicMock

    from app.jobs.claim_outcomes import JobClaim, JobClaimLostError, OutcomeAck
    from app.models.managers.job_manager import JobManager
    from app.models.scheduling.job import Job
    from app.services import task_run_operations, task_run_worker

    require_isolated_schema()
    task, reference, _ = await _publish(workspace)
    with workspace.scope():
        receipt = await task_run_service.admit_manual(
            task.id, expected_revision=reference.revision, request_key="stale"
        )
    manager = JobManager()
    job = await manager.claim_job(receipt.job_id)
    assert job is not None
    claim = JobClaim.capture(job)
    async with async_session_maker() as session, session.begin():
        row = await session.get(Job, job.id)
        row.locked_at = datetime.now(timezone.utc) - timedelta(hours=2)
    with tenant_scope(workspace.tenants[0].id):
        assert await manager.reap_stale(timeout_minutes=30) == 1
        late = await manager.mark_done(job.id, result={"synthetic": True}, claim=claim)
        assert late.acknowledgement == OutcomeAck.CLAIM_LOST
    async with async_session_maker() as session:
        run = await session.get(TaskRun, receipt.run_id)
        row = await session.get(Job, job.id)
        assert run.status == "unknown" and row.status == "failed"
        assert row.attempts == 1
        plan, methodology = task_run_worker._snapshot(run)
    provider = MagicMock(side_effect=AssertionError("stale claim must never call provider"))
    monkeypatch.setattr(task_run_operations, "_read_folder", provider)
    with tenant_scope(workspace.tenants[0].id), pytest.raises(JobClaimLostError):
        await task_run_worker._execute_step(claim, plan, methodology, plan.steps[0])
    provider.assert_not_called()


async def test_migration_refuses_populated_downgrade(workspace):
    from alembic import command
    from alembic.config import Config

    from app.core.database import engine

    require_isolated_schema()
    task, reference, _ = await _publish(workspace, scheduled=True)
    with workspace.scope():
        await task_trigger_service.configure(
            task.id, revision=reference.revision, expected_version=reference.task_version, enabled=True
        )
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        with pytest.raises(RuntimeError, match="Downgrade refused"):
            command.downgrade(config, "0159")
    async with async_session_maker() as session:
        assert await session.scalar(select(TaskTrigger.id).where(TaskTrigger.task_id == task.id)) is not None


async def test_receipt_projection_requires_visibility_and_excludes_raw_results(workspace):
    from app.services.planner.tasks import PlannerUnavailable

    require_isolated_schema()
    task, reference, _ = await _publish(workspace)
    with workspace.scope():
        receipt = await task_run_service.admit_manual(task.id, expected_revision=reference.revision, request_key="view")
    async with async_session_maker() as session, session.begin():
        run = await session.get(TaskRun, receipt.run_id)
        run.result = {"steps": {"read": {"status": "done", "count": 4, "headers": "secret"}}, "body": "private"}
    with workspace.scope():
        result = await task_run_service.get_receipt(receipt.run_id)
        assert result.result == {"steps": {"read": {"status": "done", "count": 4}}}
    with workspace.scope(1), pytest.raises(TaskRunAdmissionError, match="run_unavailable"):
        await task_run_service.get_receipt(receipt.run_id)
    with tenant_scope(workspace.tenants[0].id):
        from app.core.access_catalog import workspace_grants
        from app.core.permissions import permission_scope

        with permission_scope(workspace.other_user, member_grants=workspace_grants("viewer")):
            with pytest.raises(PlannerUnavailable):
                await task_run_service.get_receipt(receipt.run_id)


async def test_concurrent_scheduler_ticks_consume_occurrence_once(workspace):
    require_isolated_schema()
    task, reference, _ = await _publish(workspace, scheduled=True)
    with workspace.scope():
        trigger = await task_trigger_service.configure(
            task.id, revision=reference.revision, expected_version=reference.task_version, enabled=True
        )
    with tenant_scope(workspace.tenants[0].id):
        stats = await asyncio.gather(
            *(task_trigger_service.tick(workspace.tenants[0].id, trigger.next_due_at) for _ in range(2))
        )
    assert sum(item["enqueued"] for item in stats) == 1
    assert sum(item["failed"] for item in stats) == 0
    async with async_session_maker() as session:
        runs = (await session.scalars(select(TaskRun).where(TaskRun.task_id == task.id))).all()
        assert len(runs) == 1 and runs[0].job_id is not None


async def test_supported_worker_completes_with_mocked_mail(workspace, monkeypatch):
    from unittest.mock import MagicMock

    from app.jobs.dispatcher import _execute_claimed
    from app.models.managers.job_manager import JobManager
    from app.services import task_run_operations

    require_isolated_schema()
    task, reference, _ = await _publish(workspace)
    with workspace.scope():
        receipt = await task_run_service.admit_manual(
            task.id, expected_revision=reference.revision, request_key="mock-mail"
        )
    provider = MagicMock(return_value=[(42, 1, "a@example.invalid"), (42, 2, "a@example.invalid")])
    monkeypatch.setattr(task_run_operations, "_read_folder", provider)
    monkeypatch.setattr(task_run_operations, "decrypt_secret", lambda _: "synthetic-password")
    job = await JobManager.claim_job(receipt.job_id)
    assert job is not None
    outcome = await _execute_claimed(job)
    provider.assert_called_once()
    assert outcome["status"] == "done"
    async with async_session_maker() as session:
        run = await session.get(TaskRun, receipt.run_id)
        assert run.status == "done"
        assert run.checkpoint_encrypted is None
        assert run.result["steps"] == {"read": {"status": "done", "count": 2}, "group": {"status": "done", "count": 1}}
    with workspace.scope():
        visible = await task_run_service.get_receipt(receipt.run_id)
        assert visible.result["coverage"] == "complete"


async def test_manual_scheduler_race_keeps_one_active_job_and_advances_cursor(workspace, monkeypatch):
    from app.models.scheduling.job import Job
    from app.services import task_run_admission

    require_isolated_schema()
    task, reference, _ = await _publish(workspace, scheduled=True)
    with workspace.scope():
        trigger = await task_trigger_service.configure(
            task.id, revision=reference.revision, expected_version=reference.task_version, enabled=True
        )
    entered = asyncio.Event()
    release = asyncio.Event()
    original_insert = task_run_admission._insert_run

    async def gated_insert(session, **kwargs):
        if kwargs.get("manual_actor_id"):
            entered.set()
            await asyncio.wait_for(release.wait(), 5)
        return await original_insert(session, **kwargs)

    monkeypatch.setattr(task_run_admission, "_insert_run", gated_insert)

    async def manual_admission():
        with workspace.scope():
            return await task_run_service.admit_manual(
                task.id, expected_revision=reference.revision, request_key="race-manual"
            )

    async def schedule_tick():
        with tenant_scope(workspace.tenants[0].id):
            return await task_trigger_service.tick(workspace.tenants[0].id, trigger.next_due_at)

    manual = asyncio.create_task(manual_admission())
    scheduler = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        scheduler = asyncio.create_task(schedule_tick())
        await asyncio.sleep(0.2)
        assert not scheduler.done(), "scheduler should wait while admission holds workspace locks"
    finally:
        release.set()
        receipt = await asyncio.wait_for(manual, 5)
    assert await asyncio.wait_for(scheduler, 5) == {"due": 1, "enqueued": 0, "failed": 0}
    async with async_session_maker() as session:
        runs = (await session.scalars(select(TaskRun).where(TaskRun.task_id == task.id))).all()
        jobs = (await session.scalars(select(Job).where(Job.task_run_id.in_([run.id for run in runs])))).all()
        current = await session.get(TaskTrigger, trigger.trigger_id)
        assert len(runs) == 2 and len(jobs) == 1
        assert {run.status for run in runs} == {"pending", "skipped"}
        assert jobs[0].id == receipt.job_id
        assert current.next_due_at > trigger.next_due_at


async def test_mid_run_membership_revocation_stops_the_next_step(workspace, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from app.jobs.dispatcher import _execute_claimed
    from app.models import TenantUser
    from app.models.managers.job_manager import JobManager
    from app.services import task_run_operations, task_run_worker

    require_isolated_schema()
    task, reference, _ = await _publish(workspace)
    with workspace.scope():
        receipt = await task_run_service.admit_manual(task.id, expected_revision=reference.revision, request_key="loss")
    provider = MagicMock(return_value=[(42, 1, "a@example.invalid")])
    group = AsyncMock(side_effect=AssertionError("revoked actor must not execute the next step"))
    monkeypatch.setattr(task_run_operations, "_read_folder", provider)
    monkeypatch.setattr(task_run_operations, "decrypt_secret", lambda _: "synthetic-password")
    monkeypatch.setattr(
        task_run_operations, "OPERATIONS", {**task_run_operations.OPERATIONS, "mail.group_by_sender": group}
    )
    original_execute = task_run_worker._execute_step

    async def revoke_after_first_step(claim, plan, methodology, step):
        await original_execute(claim, plan, methodology, step)
        if step.name == "read":
            async with async_session_maker() as session, session.begin():
                membership = await session.scalar(
                    select(TenantUser).where(
                        TenantUser.tenant_id == workspace.tenants[0].id, TenantUser.user_id == workspace.user.id
                    )
                )
                membership.is_active = False

    monkeypatch.setattr(task_run_worker, "_execute_step", revoke_after_first_step)
    job = await JobManager.claim_job(receipt.job_id)
    assert job is not None
    outcome = await _execute_claimed(job)
    provider.assert_called_once()
    group.assert_not_awaited()
    assert outcome["status"] == "failed"
    async with async_session_maker() as session:
        run = await session.get(TaskRun, receipt.run_id)
        assert run.status == "failed"
        assert run.result == {"steps": {"read": {"status": "done", "count": 1}}, "code": "task_authorization_lost"}
        assert run.checkpoint_encrypted is None


async def test_real_serialization_rollback_retries_without_replaying_committed_read(workspace, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from unittest.mock import MagicMock

    from sqlalchemy import text

    from app.jobs.dispatcher import _execute_claimed
    from app.models.managers.job_manager import JobManager
    from app.models.scheduling.job import Job
    from app.services import task_run_operations

    require_isolated_schema()
    task, reference, _ = await _publish(workspace)
    with workspace.scope():
        receipt = await task_run_service.admit_manual(
            task.id, expected_revision=reference.revision, request_key="rollback"
        )
    provider = MagicMock(return_value=[(42, 1, "a@example.invalid")])
    monkeypatch.setattr(task_run_operations, "_read_folder", provider)
    monkeypatch.setattr(task_run_operations, "decrypt_secret", lambda _: "synthetic-password")
    group = task_run_operations.group_by_sender
    calls = 0

    async def rollback_once(session, context, step, inputs, resources):
        nonlocal calls
        calls += 1
        if calls == 1:
            run = await session.get(TaskRun, context.run_id)
            run.result = {"rollback_sentinel": "must not commit"}
            await session.flush()
            await session.execute(text("DO $$ BEGIN RAISE EXCEPTION 'synthetic retry' USING ERRCODE = '40001'; END $$"))
        return await group(session, context, step, inputs, resources)

    monkeypatch.setattr(
        task_run_operations, "OPERATIONS", {**task_run_operations.OPERATIONS, "mail.group_by_sender": rollback_once}
    )
    manager = JobManager()
    job = await manager.claim_job(receipt.job_id)
    first = await _execute_claimed(job)
    assert first["status"] == "pending"
    async with async_session_maker() as session, session.begin():
        run = await session.get(TaskRun, receipt.run_id)
        row = await session.get(Job, receipt.job_id)
        assert run.status == "pending" and row.attempts == 1
        assert run.result == {"steps": {"read": {"status": "done", "count": 1}}}
        assert run.checkpoint_encrypted is not None
        row.run_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    retry = await manager.claim_job(receipt.job_id)
    assert retry is not None and retry.attempts == 2
    second = await _execute_claimed(retry)
    assert second["status"] == "done"
    provider.assert_called_once()
    assert calls == 2
    async with async_session_maker() as session:
        run = await session.get(TaskRun, receipt.run_id)
        assert run.status == "done" and run.checkpoint_encrypted is None
        assert run.result["steps"] == {"read": {"status": "done", "count": 1}, "group": {"status": "done", "count": 1}}


async def test_receipt_and_idempotency_survive_actual_job_history_pruning(workspace, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from unittest.mock import MagicMock

    from app.jobs.dispatcher import _execute_claimed
    from app.jobs.lifecycle import cleanup_terminal_job_history
    from app.models.managers.job_manager import JobManager
    from app.models.scheduling.job import Job
    from app.services import task_run_operations

    require_isolated_schema()
    task, reference, _ = await _publish(workspace)
    with workspace.scope():
        receipt = await task_run_service.admit_manual(
            task.id, expected_revision=reference.revision, request_key="prune-replay"
        )
    provider = MagicMock(return_value=[(42, 1, "a@example.invalid")])
    monkeypatch.setattr(task_run_operations, "_read_folder", provider)
    monkeypatch.setattr(task_run_operations, "decrypt_secret", lambda _: "synthetic-password")
    job = await JobManager.claim_job(receipt.job_id)
    assert (await _execute_claimed(job))["status"] == "done"
    with tenant_scope(workspace.tenants[0].id):
        deleted = await cleanup_terminal_job_history(
            tenant_id=workspace.tenants[0].id, now=datetime.now(timezone.utc) + timedelta(days=2)
        )
    assert deleted == 1
    async with async_session_maker() as session:
        assert await session.get(Job, receipt.job_id) is None
        assert await session.get(TaskRun, receipt.run_id) is not None
    with workspace.scope():
        visible = await task_run_service.get_receipt(receipt.run_id)
        replay = await task_run_service.admit_manual(
            task.id, expected_revision=reference.revision, request_key="prune-replay"
        )
    assert visible.status == "done" and visible.result["coverage"] == "complete"
    assert replay.replayed and replay.run_id == receipt.run_id and replay.job_id == receipt.job_id
    provider.assert_called_once()


async def test_scheduler_manual_race_keeps_one_active_job_when_scheduler_wins(workspace, monkeypatch):
    from app.models.scheduling.job import Job
    from app.services import task_run_admission

    require_isolated_schema()
    task, reference, _ = await _publish(workspace, scheduled=True)
    with workspace.scope():
        trigger = await task_trigger_service.configure(
            task.id, revision=reference.revision, expected_version=reference.task_version, enabled=True
        )
    entered = asyncio.Event()
    release = asyncio.Event()
    original_insert = task_run_admission._insert_run

    async def gated_insert(session, **kwargs):
        if kwargs.get("trigger_id"):
            entered.set()
            await asyncio.wait_for(release.wait(), 5)
        return await original_insert(session, **kwargs)

    monkeypatch.setattr(task_run_admission, "_insert_run", gated_insert)

    async def schedule_tick():
        with tenant_scope(workspace.tenants[0].id):
            return await task_trigger_service.tick(workspace.tenants[0].id, trigger.next_due_at)

    async def manual_admission():
        with workspace.scope():
            return await task_run_service.admit_manual(
                task.id, expected_revision=reference.revision, request_key="race-scheduler"
            )

    scheduler = asyncio.create_task(schedule_tick())
    manual = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        manual = asyncio.create_task(manual_admission())
        await asyncio.sleep(0.2)
        assert not manual.done(), "manual admission should wait for the scheduler's workspace locks"
    finally:
        release.set()
        stats = await asyncio.wait_for(scheduler, 5)
    receipt = await asyncio.wait_for(manual, 5)
    assert stats == {"due": 1, "enqueued": 1, "failed": 0}
    assert receipt.status == "skipped" and receipt.job_id is None and receipt.result == {"reason": "active_run"}
    async with async_session_maker() as session:
        runs = (await session.scalars(select(TaskRun).where(TaskRun.task_id == task.id))).all()
        jobs = (await session.scalars(select(Job).where(Job.task_run_id.in_([run.id for run in runs])))).all()
        assert len(runs) == 2 and len(jobs) == 1
        assert {run.status for run in runs} == {"pending", "skipped"}
        assert next(run for run in runs if run.job_id).trigger_id == trigger.trigger_id


async def test_effectful_plan_fails_admission_without_creating_run_or_job(workspace):
    from app.models.scheduling.job import Job
    from app.services.execution_plan_contracts import ExecutionPlanError

    require_isolated_schema()
    connection_id, pin = await _connection(workspace)
    with workspace.scope():
        task = await task_service.create(CreateTask(title="Synthetic unsupported write"))
        reference = await execution_plan_service.publish(
            task.id, task.version, json.dumps(mail_plan(connection_id, pin))
        )
        with pytest.raises(ExecutionPlanError, match="unsupported_execution_operation"):
            await task_run_service.admit_manual(task.id, expected_revision=reference.revision, request_key="write")
    async with async_session_maker() as session:
        assert (await session.scalars(select(TaskRun).where(TaskRun.task_id == task.id))).all() == []
        assert (await session.scalars(select(Job).where(Job.tenant_id == workspace.tenants[0].id))).all() == []


async def test_paid_plan_fails_admission_without_creating_run_or_job(workspace, monkeypatch):
    from types import SimpleNamespace

    from app.models import AgentScenario
    from app.models.scheduling.job import Job
    from app.services import task_execution_plans
    from app.services.execution_plan_contracts import map_social_plan

    require_isolated_schema()

    async def allow_synthetic_vk(*args, **kwargs):
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr(task_execution_plans, "check_canonical_read_access", allow_synthetic_vk)
    async with async_session_maker() as session, session.begin():
        scenario = AgentScenario(tenant_id=workspace.tenants[0].id, name="Synthetic", base_prompt="Synthetic only")
        session.add(scenario)
        await session.flush()
        scenario_id = scenario.id
    plan = map_social_plan(
        {
            "task_plan": {
                "source_scope": "discovery",
                "collection": {"queries": ["synthetic"]},
                "scenario_id": scenario_id,
                "vk_access": {"mode": "api"},
            },
            "collect_before_analyze": True,
        },
        job_type="analyze",
    )
    with workspace.scope():
        task = await task_service.create(CreateTask(title="Synthetic paid plan"))
        reference = await execution_plan_service.publish(task.id, task.version, plan.model_dump_json())
        with pytest.raises(TaskRunAdmissionError, match="payer_unavailable"):
            await task_run_service.admit_manual(task.id, expected_revision=reference.revision, request_key="paid")
    async with async_session_maker() as session:
        assert (await session.scalars(select(TaskRun).where(TaskRun.task_id == task.id))).all() == []
        assert (await session.scalars(select(Job).where(Job.tenant_id == workspace.tenants[0].id))).all() == []
