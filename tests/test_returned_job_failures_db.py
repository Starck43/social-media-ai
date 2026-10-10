"""Prepared PostgreSQL persistence tests; no provider calls.

Use the shared POSTGRES_URL with an isolated DB_TEST_SCHEMA via conftest.
These cases were not executed in the authoring sandbox.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.permissions import get_current_user, has_permission, service_permission_scope
from app.core.tenant_context import tenant_scope
from app.jobs import dispatcher
from app.models import AgentTask, Job, Tenant

pytestmark = pytest.mark.tenancy


@pytest.fixture
async def workspace(monkeypatch):
    tenant = await Tenant.objects.create(slug=f"returned-failure-{uuid4().hex}", name="Returned failure test", plan="business")
    monkeypatch.setattr(dispatcher, "_notify_job_result", AsyncMock())
    try:
        yield tenant
    finally:
        await Tenant.objects.delete_by_id(tenant.id)


async def claimed_job(tenant, job_type="learn"):
    with tenant_scope(tenant.id):
        with service_permission_scope("agenttask", "create"):
            task = await AgentTask.objects.create(
                name=f"returned-failure-{uuid4().hex}", cron_expr="0 0 * * *", job_type=job_type, payload={}, is_active=False
            )
        assert not has_permission(get_current_user(), "agenttask", "create")
        job = await Job.objects.create(
            job_type=job_type, agent_task_id=task.id, payload={}, status="running", attempts=1,
            max_attempts=3, started_at=datetime.now(timezone.utc), run_at=datetime.now(timezone.utc)
        )
    return task, job


def failed_handler(result):
    """Assert arrange authority does not reach dispatcher effects."""
    async def fail(payload):
        assert get_current_user() is None
        assert not has_permission(None, "agenttask", "create")
        return result

    return AsyncMock(side_effect=fail)


async def test_declared_failure_persists_job_and_task_failure_with_known_cost(workspace):
    task, job = await claimed_job(workspace)
    handler = failed_handler({"status": "failed", "error": "invalid_structured_output", "llm_cost": 0.031})
    await dispatcher.execute_job(job, handler, allow_retry=True)
    with tenant_scope(workspace.id):
        stored = await Job.objects.get(id=job.id)
        task = await AgentTask.objects.get(id=task.id)
        assert stored.status == "failed" and stored.finished_at is not None
        assert stored.error == "invalid_structured_output" and float(stored.llm_cost) == pytest.approx(0.031)
        assert stored.result == {"status": "failed", "error": "invalid_structured_output", "llm_cost": 0.031}
        assert task.last_status == "failed" and task.last_error == "invalid_structured_output"
    handler.assert_awaited_once()
    assert dispatcher._notify_job_result.await_args.kwargs["success"] is False


async def test_unknown_provider_error_is_not_persisted_as_raw_text(workspace):
    _, job = await claimed_job(workspace, "reflect")
    handler = failed_handler({"status": "failed", "error": "PRIVATE-PROVIDER-TEXT", "llm_cost": None})
    await dispatcher.execute_job(job, handler)
    with tenant_scope(workspace.id):
        stored = await Job.objects.get(id=job.id)
        assert stored.status == "failed" and stored.error == "handler_returned_failure"
        assert stored.result["llm_cost"] is None
        assert "PRIVATE" not in str(stored.result) + stored.error
    assert "PRIVATE" not in str(dispatcher._notify_job_result.await_args)


async def test_inline_path_returns_failed_instead_of_done(workspace, monkeypatch):
    _, job = await claimed_job(workspace)
    handler = failed_handler({"status": "failed", "error": "llm_call_failed", "llm_cost": None})
    monkeypatch.setitem(dispatcher.HANDLERS, "learn", handler)
    result = await dispatcher._run_claimed_inline(job)
    assert result["status"] == "failed" and result["error"] == "llm_call_failed"
    assert result["job_id"] == job.id
