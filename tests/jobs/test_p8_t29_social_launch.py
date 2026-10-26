"""P8-T29 social launch receipts and payer boundaries.

Requires the ordinary root conftest and its approved test_schema redirect.
The tests enqueue mockable work but never call a VK API or execute a job.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4
from unittest.mock import AsyncMock

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

import pytest
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import (
    AgentScenario,
    AgentTask,
    Job,
    PersonalBillingAccount,
    Platform,
    PlatformRole,
    Source,
    Tenant,
    TaskBillingPayer,
    User,
    WorkspaceRole,
)
from app.models.managers.tenant_manager import tenant_users, tenants
from app.models.managers.personal_billing_manager import PersonalBillingUnavailable
from app.services.billing.payer import configure_current_personal_account
from app.services.task_launch import launch_identity, launch_social_task
from app.services.task_plans import apply_task_plan, materialize_task_plan_window, parse_task_plan, task_plan_block
from app.types import SourceType

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


class Workspace:
    def __init__(self, tenant: Tenant, user: User, owner_role_id: int) -> None:
        self.tenant = tenant
        self.user = user
        self.owner_role_id = owner_role_id
        self.task_ids: list[int] = []
        self.source_ids: list[int] = []
        self.scenario_ids: list[int] = []

    @classmethod
    async def create(cls) -> "Workspace":
        platform_role = await PlatformRole.objects.filter(codename="user").first()
        owner_role = await WorkspaceRole.objects.filter(codename="owner").first()
        assert platform_role is not None and owner_role is not None
        suffix = uuid4().hex
        tenant = await tenants.create(
            slug=f"p8t29-{suffix}", name="P8-T29 test workspace", plan="business", is_active=True
        )
        user = await User.objects.create_user(
            username=f"p8t29-{suffix}",
            password=uuid4().hex,
            email=f"p8t29-{suffix}@example.invalid",
            role_id=platform_role.id,
            is_active=True,
        )
        with tenant_scope(tenant.id):
            await tenant_users.create(
                tenant_id=tenant.id,
                channel="web",
                external_user_id=str(user.id),
                user_id=user.id,
                role_id=owner_role.id,
                ai_chat_enabled=True,
                is_active=True,
            )
        return cls(tenant, user, owner_role.id)

    def actor_scope(self):
        return _actor_scope(self)

    async def scenario(self) -> AgentScenario:
        with self.actor_scope():
            row = await AgentScenario.objects.create(
                name=f"P8-T29 scenario {uuid4().hex[:10]}",
                analysis_types=["sentiment"],
                content_types=["text"],
                scope={},
                is_active=True,
            )
        self.scenario_ids.append(row.id)
        return row

    async def source(self) -> Source:
        platform = await Platform.objects.filter(platform_type="vk", is_active=True).first()
        assert platform is not None
        with self.actor_scope():
            row = await Source.objects.create(
                name=f"P8-T29 source {uuid4().hex[:10]}",
                platform_id=platform.id,
                external_id=f"p8t29-{uuid4().hex}",
                source_type=SourceType.GROUP,
                is_active=True,
            )
        self.source_ids.append(row.id)
        return row

    async def cleanup(self) -> None:
        # This fixture owns a fresh tenant, so clean every task in that tenant,
        # including one created just before an assertion failed to record its ID.
        with tenant_scope(bypass=True):
            tasks = await AgentTask.objects.filter(tenant_id=self.tenant.id)
            for task in reversed(tasks):
                await AgentTask.objects.delete_by_id(task.id)
            assert await AgentTask.objects.filter(tenant_id=self.tenant.id) == []
            for scenario_id in reversed(self.scenario_ids):
                await AgentScenario.objects.delete_by_id(scenario_id)
            for source_id in reversed(self.source_ids):
                await Source.objects.delete_by_id(source_id)
            assert await TaskBillingPayer.objects.filter(tenant_id=self.tenant.id).count() == 0
            await PersonalBillingAccount.objects.delete(user_id=self.user.id)
            await tenants.delete_by_id(self.tenant.id)
            await User.objects.delete_by_id(self.user.id)


@contextmanager
def _actor_scope(workspace: Workspace):
    with tenant_scope(workspace.tenant.id), permission_scope(workspace.user, is_owner=True):
        yield


@pytest.fixture
async def workspace(request, monkeypatch):
    # Consent persistence belongs to the existing personal-billing contract.
    # This suite tests the launch boundary and its committed task/job races.
    # Double only a successful consent's append-only side effect; keep the
    # missing-account and membership-refusal paths real. This prevents immortal
    # synthetic consent rows in a shared schema without weakening its triggers.
    consent = None
    if request.node.name in {
        "test_same_request_concurrently_creates_one_task_and_one_job",
        "test_request_key_cannot_be_reused_for_different_conditions",
        "test_launch_receipt_survives_job_pruning",
        "test_manual_rerun_does_not_replace_automatic_request_receipt",
    }:
        consent = AsyncMock()
        monkeypatch.setattr("app.services.billing.payer.consent_current_user_as_task_payer", consent)
    value = await Workspace.create()
    try:
        yield value
    finally:
        await value.cleanup()
    if consent is not None:
        assert consent.await_count > 0
        assert all(call.args[0] in value.task_ids for call in consent.await_args_list)


def _plan(scenario_id: int, source_id: int) -> dict:
    return {
        "goal": "Find repair requests",
        "platforms": ["vk"],
        "source_scope": "selected",
        "source_ids": [source_id],
        "scenario_id": scenario_id,
        "collection": {"content_types": ["posts"], "limit": 20, "queries": ["repair"]},
        "trigger": {"type": "once"},
    }


def test_launch_key_is_scoped_to_actor_workspace_and_conditions():
    plan = {"source_scope": "all", "trigger": {"type": "once"}}
    first = launch_identity(10, 20, "request-123", plan, "analyze")

    assert launch_identity(10, 20, "request-123", plan, "analyze") == first
    assert launch_identity(11, 20, "request-123", plan, "analyze")[0] != first[0]
    assert launch_identity(10, 21, "request-123", plan, "analyze")[0] != first[0]
    assert launch_identity(10, 20, "request-123", {**plan, "limit": 1}, "analyze")[1] != first[1]
    with pytest.raises(ValueError, match="request_id"):
        launch_identity(10, 20, "short", plan, "analyze")


def test_new_plan_defaults_to_a_bounded_one_day_window_and_materializes_once():
    plan = parse_task_plan({"source_scope": "all", "trigger": {"type": "once"}})
    block = task_plan_block(plan)
    assert block["window"] == {"type": "rolling", "days": 1}

    payload = apply_task_plan({}, block, job_type="analyze")
    now = datetime(2026, 10, 25, 23, 50, tzinfo=timezone.utc)
    first = materialize_task_plan_window(payload, now)
    replay = materialize_task_plan_window(first, now + timedelta(days=4))
    assert first["cli_dates"] == {"start_date": "2026-10-25", "end_date": "2026-10-25"}
    assert replay["cli_dates"] == first["cli_dates"]


async def test_same_request_concurrently_creates_one_task_and_one_job(workspace):
    scenario = await workspace.scenario()
    source = await workspace.source()
    with workspace.actor_scope():
        await configure_current_personal_account("20")
        plan = _plan(scenario.id, source.id)
        first, second = await __import__("asyncio").gather(
            launch_social_task(plan, request_id="request-concurrent-01"),
            launch_social_task(plan, request_id="request-concurrent-01"),
        )

    assert {first["status"], second["status"]} == {"queued", "replayed"}
    assert first["task_id"] == second["task_id"]
    assert first["job_id"] == second["job_id"]
    with tenant_scope(workspace.tenant.id):
        jobs = await Job.objects.filter(agent_task_id=first["task_id"])
        task = await AgentTask.objects.get(id=first["task_id"])
    assert len(jobs) == 1
    assert task is not None and task.launch_job_id == jobs[0].id
    workspace.task_ids.append(first["task_id"])


async def test_request_key_cannot_be_reused_for_different_conditions(workspace):
    scenario = await workspace.scenario()
    source = await workspace.source()
    with workspace.actor_scope():
        await configure_current_personal_account("20")
        plan = _plan(scenario.id, source.id)
        created = await launch_social_task(plan, request_id="request-mismatch-01")
        changed = {**plan, "goal": "Find insurance requests"}
        with pytest.raises(ValueError, match="different task conditions"):
            await launch_social_task(changed, request_id="request-mismatch-01")

    workspace.task_ids.append(created["task_id"])
    with tenant_scope(workspace.tenant.id):
        jobs = await Job.objects.filter(agent_task_id=created["task_id"])
    assert len(jobs) == 1


async def test_launch_receipt_survives_job_pruning(workspace):
    scenario = await workspace.scenario()
    source = await workspace.source()
    with workspace.actor_scope():
        await configure_current_personal_account("20")
        plan = _plan(scenario.id, source.id)
        created = await launch_social_task(plan, request_id="request-pruned-01")
    workspace.task_ids.append(created["task_id"])

    # Turn only this synthetic job terminal, then delete that exact row. The
    # stored task receipt must remain the deduplication source of truth.
    with tenant_scope(workspace.tenant.id):
        job = await Job.objects.get(id=created["job_id"])
        assert job is not None
        await Job.objects.update_by_id(
            job.id,
            status="done",
            finished_at=datetime.now(timezone.utc) - timedelta(days=3),
        )
        await Job.objects.delete_by_id(job.id)
    with workspace.actor_scope():
        replay = await launch_social_task(plan, request_id="request-pruned-01")

    assert replay["status"] == "replayed"
    assert replay["task_id"] == created["task_id"]
    assert replay["job_id"] == created["job_id"]
    assert replay["job_status"] == "unavailable"
    with tenant_scope(workspace.tenant.id):
        remaining = await Job.objects.filter(agent_task_id=created["task_id"])
    assert remaining == []


async def test_manual_rerun_does_not_replace_automatic_request_receipt(workspace):
    from app.jobs.enqueue import enqueue_task_run

    scenario = await workspace.scenario()
    source = await workspace.source()
    with workspace.actor_scope():
        await configure_current_personal_account("20")
        plan = _plan(scenario.id, source.id)
        first = await launch_social_task(plan, request_id="request-manual-again-01")
        workspace.task_ids.append(first["task_id"])
        await Job.objects.update_by_id(first["job_id"], status="done", finished_at=datetime.now(timezone.utc))
        task = await AgentTask.objects.get(id=first["task_id"])
        second = await enqueue_task_run(task, initiated_by_user_id=workspace.user.id)
        replay = await launch_social_task(plan, request_id="request-manual-again-01")
        refreshed = await AgentTask.objects.get(id=task.id)

    assert second.id != first["job_id"]
    assert replay["job_id"] == first["job_id"]
    assert replay["status"] == "replayed"
    assert refreshed.launch_job_id == first["job_id"]


async def test_missing_personal_payer_keeps_an_inactive_draft_without_a_job(workspace):
    scenario = await workspace.scenario()
    source = await workspace.source()
    with workspace.actor_scope():
        plan = _plan(scenario.id, source.id)
        with pytest.raises(PersonalBillingUnavailable):
            await launch_social_task(plan, request_id="request-no-payer-01")

    with tenant_scope(workspace.tenant.id):
        # The task name includes a goal prefix, so locate by the durable key.
        key, _ = launch_identity(
            workspace.tenant.id,
            workspace.user.id,
            "request-no-payer-01",
            task_plan_block(parse_task_plan(plan)),
            "analyze",
        )
        task = await AgentTask.objects.filter(launch_key=key).first()
        jobs = await Job.objects.filter(agent_task_id=task.id) if task is not None else []
    assert task is not None and task.is_active is False and task.launch_job_id is None
    assert jobs == []
    workspace.task_ids.append(task.id)


async def test_selected_source_from_another_workspace_is_refused(workspace):
    scenario = await workspace.scenario()
    foreign = await Workspace.create()
    try:
        foreign_source = await foreign.source()
        with workspace.actor_scope():
            await configure_current_personal_account("20")
            with pytest.raises(ValueError, match="outside the task's platform/workspace"):
                await launch_social_task(_plan(scenario.id, foreign_source.id), request_id="request-foreign-source-01")
        with tenant_scope(workspace.tenant.id):
            assert await AgentTask.objects.count() == 0
    finally:
        await foreign.cleanup()


async def test_revoked_membership_blocks_a_stale_interactive_scope(workspace):
    scenario = await workspace.scenario()
    source = await workspace.source()
    with workspace.actor_scope():
        await configure_current_personal_account("20")
        membership = await tenant_users.filter(
            tenant_id=workspace.tenant.id, channel="web", user_id=workspace.user.id
        ).first()
        assert membership is not None
        await tenant_users.update_by_id(membership.id, is_active=False)
        with pytest.raises(PermissionError, match="active workspace membership"):
            await launch_social_task(_plan(scenario.id, source.id), request_id="request-revoked-01")
