"""PostgreSQL receipt fencing and admitted one-off execution checks.

Run sequentially through the root conftest against its approved local
``social_manager/test_schema`` redirect. No provider, LLM, delivery or migration
is exercised here.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

_conftest_path = Path(__file__).resolve().parents[1] / "conftest.py"
_conftest = next(
    (module for module in tuple(sys.modules.values()) if getattr(module, "__file__", None) == str(_conftest_path)),
    None,
)
if _conftest is None or getattr(_conftest, "TEST_SCHEMA", None) != "test_schema":
    raise RuntimeError("STOP: root conftest/test_schema redirect required; never use --noconftest")
_target = urlsplit(getattr(_conftest, "TEST_DATABASE_URL", ""))
if (
    _target.hostname not in {"localhost", "127.0.0.1"}
    or (_target.port or 5432) != 5432
    or _target.path != "/social_manager"
):
    raise RuntimeError("STOP: only the approved local social_manager/test_schema is allowed")

from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.jobs import progress
from app.jobs.claim_outcomes import JobClaim, OutcomeAck
from app.models import AgentTask, Job, PlatformRole, Tenant, User, WorkspaceRole
from app.models.managers.job_manager import JobManager
from app.models.managers.tenant_manager import tenant_users, tenants
from app.services import task_pipeline

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


class Workspace:
    def __init__(self, tenant, user):
        self.tenant = tenant
        self.user = user
        self.jobs = []
        self.tasks = []

    @classmethod
    async def create(cls):
        platform_role = await PlatformRole.objects.filter(codename="user").first()
        workspace_role = await WorkspaceRole.objects.filter(codename="owner").first()
        assert platform_role is not None and workspace_role is not None
        suffix = uuid4().hex
        tenant = await tenants.create(
            slug=f"p8t40-checkpoint-{suffix}",
            name="P8-T40 checkpoint test",
            plan="business",
            is_active=True,
        )
        user = await User.objects.create_user(
            username=f"p8t40-checkpoint-{suffix}",
            password=uuid4().hex,
            email=f"p8t40-checkpoint-{suffix}@example.invalid",
            role_id=platform_role.id,
            is_active=True,
        )
        with tenant_scope(tenant.id):
            await tenant_users.create(
                tenant_id=tenant.id,
                channel="web",
                external_user_id=str(user.id),
                user_id=user.id,
                role_id=workspace_role.id,
                ai_chat_enabled=True,
                is_active=True,
            )
        return cls(tenant, user)

    def actor_scope(self):
        return permission_scope(self.user, is_owner=True)

    async def cleanup(self):
        with tenant_scope(bypass=True):
            for job in reversed(self.jobs):
                await Job.objects.delete_by_id(job.id)
            for task in reversed(self.tasks):
                await AgentTask.objects.delete_by_id(task.id)
            await tenants.delete_by_id(self.tenant.id)
            await User.objects.delete_by_id(self.user.id)


@pytest.fixture
async def workspace():
    value = await Workspace.create()
    try:
        yield value
    finally:
        await value.cleanup()


async def _claimed_analyze(workspace, *, task_id=None):
    manager = JobManager()
    now = datetime.now(timezone.utc)
    with tenant_scope(workspace.tenant.id):
        pending = await manager.enqueue(
            "analyze",
            agent_task_id=task_id,
            payload={},
            run_at=now,
            max_attempts=3,
        )
        claimed = await manager.claim_job(pending.id, now=now)
    assert claimed is not None and claimed.status == "running"
    workspace.jobs.append(claimed)
    return manager, claimed, JobClaim.capture(claimed)


async def test_checkpoint_survives_progress_retry_and_rejects_old_claim(workspace):
    manager, job, old_claim = await _claimed_analyze(workspace)
    checkpoint = {
        "version": 1,
        "job_id": job.id,
        "task_id": 7,
        "attempt": old_claim.attempts,
        "stages": {
            "collect": {"state": "completed", "attempt": old_claim.attempts, "result": {"counts": {"items": 3}}},
            "analyze": {"state": "started", "attempt": old_claim.attempts},
        },
    }
    with tenant_scope(workspace.tenant.id):
        token = progress.bind(progress.ProgressSession(old_claim))
        try:
            await progress.pipeline_checkpoint(checkpoint)
        finally:
            progress.unbind(token)
        assert await manager.record_progress(old_claim, {"phase": "analysis", "total": 1, "completed": 0})
        stored = await manager.get(id=job.id)
        assert stored.result["pipeline"] == checkpoint
        assert stored.result["progress"]["phase"] == "analysis"

        failed = await manager.mark_failed(job.id, error="retryable fixture failure", allow_retry=True, claim=old_claim)
        assert failed.acknowledgement == OutcomeAck.RETRY
        new_job = await manager.claim_job(job.id, now=datetime.now(timezone.utc) + timedelta(days=1))
        assert new_job is not None and new_job.attempts == old_claim.attempts + 1
        new_claim = JobClaim.capture(new_job)
        before = await manager.get(id=job.id)
        assert before.result["pipeline"] == checkpoint

        assert not await manager.record_pipeline_checkpoint(
            old_claim,
            {**checkpoint, "stages": {"digest": {"state": "completed", "result": {"text": "stale"}}}},
        )
        after_stale = await manager.get(id=job.id)
        assert after_stale.result == before.result

    loaded = task_pipeline._valid_checkpoint(after_stale.result["pipeline"], job_id=job.id, task_id=7)
    assert loaded["stages"]["collect"]["state"] == "completed"
    assert loaded["stages"]["analyze"]["state"] == "started"

    from app.jobs import progress as progress_module

    operation = AsyncMock()
    with tenant_scope(workspace.tenant.id):
        token = progress.bind(progress.ProgressSession(new_claim))
        try:
            _, prior = await task_pipeline._stage(loaded, "collect", operation, attempt=new_claim.attempts)
            assert prior == {"counts": {"items": 3}}
            with pytest.raises(task_pipeline.PipelineReviewRequired, match="pipeline_stage_outcome_requires_review"):
                await task_pipeline._stage(loaded, "analyze", operation, attempt=new_claim.attempts)
        finally:
            progress.unbind(token)
        final = await manager.get(id=job.id)
    operation.assert_not_awaited()
    assert final.result["pipeline"]["stages"]["analyze"]["state"] == "review_required"
    assert progress_module.read_snapshot(final.result, final.attempts) is not None


async def test_claimed_once_task_survives_admission_deactivation_but_rechecks_payer(workspace, monkeypatch):
    manager = JobManager()
    with workspace.actor_scope():
        task = await AgentTask.objects.create(
            name=f"P8-T40 one-shot {uuid4().hex}",
            job_type="analyze",
            cron_expr="@once",
            payload={},
            is_active=False,
        )
        workspace.tasks.append(task)
        manager, job, claim = await _claimed_analyze(workspace, task_id=task.id)
        with tenant_scope(workspace.tenant.id):
            await AgentTask.objects.filter(id=task.id).update(launch_job_id=job.id)
            task_row = await AgentTask.objects.get(id=task.id)
        assert task_row.is_active is False

    payer = SimpleNamespace(user_id=workspace.user.id)
    monkeypatch.setattr("app.services.billing.payer.resolve_scheduled_task_payer", AsyncMock(return_value=payer))
    monkeypatch.setattr("app.core.personal_catalog_context.current_catalog_owner_id", lambda: workspace.user.id)
    task_view = SimpleNamespace(
        id=task.id,
        tenant_id=workspace.tenant.id,
        cron_expr="@once",
        launch_job_id=job.id,
    )
    payload = {
        "job_id": job.id,
        "attempts": claim.attempts,
        "agent_task_id": task.id,
        "_trusted_initiated_by_user_id": workspace.user.id,
    }
    with workspace.actor_scope():
        assert await task_pipeline._authorized_task(task_view, payload, stage="analyze") == workspace.user.id

        revoked = AsyncMock(side_effect=RuntimeError("payer consent revoked"))
        monkeypatch.setattr("app.services.billing.payer.resolve_scheduled_task_payer", revoked)
        with pytest.raises(PermissionError, match="pipeline_payer_unavailable"):
            await task_pipeline._authorized_task(task_view, payload, stage="digest")
        revoked.assert_awaited_once_with(task_id=task.id, tenant_id=workspace.tenant.id)
