"""Tests for job outcome bookkeeping: AgentTask.last_status and notifications.

Coverage:
- a failing job marks its AgentTask failed and creates an API_ERROR notification;
- a successful job marks its AgentTask ok and creates a REPORT_READY notification;
- AgentTaskManager.record_result writes last_status/last_error/last_run_at.
"""

import secrets
from datetime import datetime, timezone

import pytest

from app.core.tenant_context import tenant_scope
from app.jobs.dispatcher import _notify_job_result, execute_job
from app.models import AgentTask, Job, Notification
from app.models.managers.agent_task_manager import AgentTaskManager
from app.models.managers.tenant_manager import tenants


def _name(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4)}"


async def _make_task(tenant_id: int) -> AgentTask:
    with tenant_scope(bypass=True):
        return await AgentTask.objects.create(
            name=_name("jobnotify"),
            tenant_id=tenant_id,
            job_type="collect",
            cron_expr="@once",
            payload={},
            is_active=True,
        )


async def _make_job(tenant_id: int, task_id: int | None, *, max_attempts: int = 1) -> Job:
    with tenant_scope(bypass=True):
        pending = await Job.objects.create(
            job_type="collect",
            payload={},
            tenant_id=tenant_id,
            agent_task_id=task_id,
            run_at=datetime.now(timezone.utc),
            max_attempts=max_attempts,
        )
        claimed = await Job.objects.claim_job(pending.id)
        assert claimed is not None and claimed.status == "running"
        return claimed


@pytest.fixture
async def _tenant():
    with tenant_scope(bypass=True):
        t = await tenants.create(name=f"JobNotify {secrets.token_hex(4)}", slug=f"jn{secrets.token_hex(4)}")
        yield t.id
        await tenants.delete_by_id(t.id)


# ── AgentTaskManager.record_result ──────────────────────────────────────────


async def test_record_result_sets_status_and_error(_tenant):
    with tenant_scope(bypass=True):
        task = await _make_task(_tenant)
        await AgentTaskManager().record_result(task.id, status="failed", error="boom")
        task = await AgentTask.objects.get(id=task.id)
        assert task.last_status == "failed"
        assert task.last_error == "boom"
        assert task.last_run_at is not None


# ── _notify_job_result (DB) ─────────────────────────────────────────────────


async def test_notify_failure_creates_api_error(_tenant):
    with tenant_scope(bypass=True):
        task = await _make_task(_tenant)
        job = await _make_job(_tenant, task.id)
        with tenant_scope(_tenant):
            await _notify_job_result(job, success=False, error="auth failed")
        notes = await Notification.objects.filter(
            related_entity_type="task", related_entity_id=task.id, notification_type="API_ERROR"
        )
        assert len(notes) == 1
        assert "не выполнена" in notes[0].message


async def test_notify_success_creates_report_ready(_tenant):
    with tenant_scope(bypass=True):
        task = await _make_task(_tenant)
        job = await _make_job(_tenant, task.id)
        with tenant_scope(_tenant):
            await _notify_job_result(job, success=True, result={"sources": 2, "items": 5})
        notes = await Notification.objects.filter(
            related_entity_type="task", related_entity_id=task.id, notification_type="REPORT_READY"
        )
        assert len(notes) == 1
        assert "успешно завершена" in notes[0].message


# ── execute_job end-to-end ──────────────────────────────────────────────────


async def test_execute_job_failure_updates_task_and_notifies(_tenant):
    async def boom(_payload):
        raise RuntimeError("kaboom")

    with tenant_scope(bypass=True):
        task = await _make_task(_tenant)
        job = await _make_job(_tenant, task.id, max_attempts=1)

    with tenant_scope(_tenant):
        await execute_job(job, boom)

    with tenant_scope(bypass=True):
        job = await Job.objects.get(id=job.id)
        assert job.status == "failed"
        assert job.error == "kaboom"
        task = await AgentTask.objects.get(id=task.id)
        assert task.last_status == "failed"
        assert task.last_error == "kaboom"
        notes = await Notification.objects.filter(
            related_entity_type="task", related_entity_id=task.id, notification_type="API_ERROR"
        )
        assert len(notes) == 1


async def test_execute_job_success_updates_task_and_notifies(_tenant):
    async def ok_handler(_payload):
        return {"sources": 1, "collected": 1}

    with tenant_scope(bypass=True):
        task = await _make_task(_tenant)
        job = await _make_job(_tenant, task.id)

    with tenant_scope(_tenant):
        await execute_job(job, ok_handler)

    with tenant_scope(bypass=True):
        job = await Job.objects.get(id=job.id)
        assert job.status == "done"
        task = await AgentTask.objects.get(id=task.id)
        assert task.last_status == "ok"
        assert task.last_error is None
        notes = await Notification.objects.filter(
            related_entity_type="task", related_entity_id=task.id, notification_type="REPORT_READY"
        )
        assert len(notes) == 1


async def test_execute_job_skipped_does_not_notify(_tenant):
    """Scheduled skips (learn below threshold, digest already sent) stay silent."""

    async def skip_handler(_payload):
        return {"status": "skipped", "reason": "cost_cap", "llm_cost": 0.0}

    with tenant_scope(bypass=True):
        task = await _make_task(_tenant)
        job = await _make_job(_tenant, task.id)

    with tenant_scope(_tenant):
        await execute_job(job, skip_handler)

    with tenant_scope(bypass=True):
        job = await Job.objects.get(id=job.id)
        assert job.status == "done"
        task = await AgentTask.objects.get(id=task.id)
        assert task.last_status == "ok"
        notes = await Notification.objects.filter(
            related_entity_type="task", related_entity_id=task.id, notification_type="REPORT_READY"
        )
        assert len(notes) == 0
