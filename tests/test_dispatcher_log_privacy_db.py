"""Prepared PostgreSQL cases; not executed in the authoring sandbox.

Use common POSTGRES_URL + isolated DB_TEST_SCHEMA through conftest.
The handler is mocked; notifications use the real DB-only default service.
"""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.tenant_context import tenant_scope
from app.core.permissions import permission_scope
from app.jobs import dispatcher
from app.models import AgentTask, Job, Notification, Tenant, User
from app.services.notifications import service

pytestmark = pytest.mark.tenancy
SECRET = "PRIVATE-CUSTOMER-AND-FAKE-TOKEN"


@pytest.fixture
async def workspace(monkeypatch):
    # The dispatcher must never request messenger delivery for these notifications.
    if service.MESSENGER_AVAILABLE:
        monkeypatch.setattr(service.messenger_service, "send_notification", AsyncMock(side_effect=AssertionError("No live sends")))
    tenant = await Tenant.objects.create(slug=f"log-privacy-{uuid4().hex}", name="Log privacy test", plan="business")
    try:
        yield tenant
    finally:
        await Tenant.objects.delete_by_id(tenant.id)


@pytest.fixture
async def workspace_owner(workspace):
    # Get an active user (e.g., the first one)
    user = await User.objects.filter(is_active=True).limit(1).first()
    if user is None:
        raise RuntimeError("No active user found in test database")
    with tenant_scope(workspace.id):
        with permission_scope(user, is_owner=True):
            yield


@pytest.mark.parametrize("retry", [False, True], ids=["terminal", "retry"])
async def test_exception_logs_and_notification_safe_without_changing_audit(workspace, retry, caplog, workspace_owner):
    with tenant_scope(workspace.id):
        task = await AgentTask.objects.create(
            name=f"log-privacy-{uuid4().hex}", cron_expr="0 0 * * *", job_type="learn", payload={}, is_active=False
        )
        job = await Job.objects.create(
            job_type="learn", agent_task_id=task.id, payload={}, status="running", attempts=1,
            max_attempts=3, started_at=datetime.now(timezone.utc), run_at=datetime.now(timezone.utc)
        )
    handler = AsyncMock(side_effect=RuntimeError(SECRET))
    with caplog.at_level(logging.INFO, logger=dispatcher.logger.name):
        await dispatcher.execute_job(job, handler, allow_retry=retry)
    owned_logs = [record for record in caplog.records if record.name == dispatcher.logger.name]
    assert owned_logs
    for record in owned_logs:
        assert SECRET not in record.getMessage() and SECRET not in repr(record.args)
        assert record.exc_info is None and record.exc_text is None
    with tenant_scope(workspace.id):
        stored = await Job.objects.get(id=job.id)
        updated_task = await AgentTask.objects.get(id=task.id)
        notification = await Notification.objects.filter(related_entity_type="task", related_entity_id=task.id).first()
        # This package intentionally does NOT sanitize existing DB audit columns.
        assert stored.error == SECRET
        if retry:
            assert stored.status == "pending" and stored.run_at > job.run_at
            assert notification is None
        else:
            assert stored.status == "failed" and stored.finished_at is not None
            assert updated_task.last_status == "failed" and updated_task.last_error == SECRET
            assert notification is not None and notification.tenant_id == workspace.id
            assert SECRET not in notification.title + notification.message
            assert dispatcher._FAILURE_MESSAGE in notification.message
    handler.assert_awaited_once()
    if service.MESSENGER_AVAILABLE:
        service.messenger_service.send_notification.assert_not_awaited()