"""DB-backed admission quota contracts for typed task launches.

Run only through the serialized root-conftest ``test_schema`` route. These tests
create isolated workspaces and remove all tasks/jobs and principals afterward.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
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
_test_schema = getattr(_conftest, "TEST_SCHEMA", None) if _conftest is not None else None
if _conftest is None or _test_schema not in {"test_schema", "test_schema_p11_t08"}:
    raise RuntimeError("STOP: approved root conftest schema redirect required; never use --noconftest")
_target = urlsplit(getattr(_conftest, "TEST_DATABASE_URL", ""))
if (
    _target.hostname not in {"localhost", "127.0.0.1"}
    or (_target.port or 5432) != 5432
    or _target.path != "/social_manager"
):
    raise RuntimeError("STOP: only approved local social_manager test schemas are allowed")

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
    TaskBillingPayer,
    Tenant,
    User,
    WorkspaceRole,
)
from app.models.managers.agent_task_manager import AgentTaskManager
from app.models.managers.tenant_manager import tenant_users, tenants
from app.services.billing.payer import configure_current_personal_account
from app.services.task_contracts import TaskLaunchError
from app.services.task_launch import launch_identity, launch_social_task
from app.services.task_plans import apply_task_plan, parse_task_plan, task_plan_block
from app.types import SourceType

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


class AdmissionWorkspace:
    def __init__(self, tenant: Tenant, users: list[User]) -> None:
        self.tenant = tenant
        self.users = users
        self.scenario_ids: list[int] = []
        self.source_ids: list[int] = []

    @classmethod
    async def create(cls) -> "AdmissionWorkspace":
        platform_role = await PlatformRole.objects.filter(codename="user").first()
        owner_role = await WorkspaceRole.objects.filter(codename="owner").first()
        assert platform_role is not None and owner_role is not None
        suffix = uuid4().hex
        tenant = await tenants.create(
            slug=f"p11t08-{suffix}", name="P11-T08 admission test", plan="business", is_active=True
        )
        user = None
        try:
            user = await cls._create_user(suffix, platform_role.id)
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
            return cls(tenant, [user])
        except Exception:
            with tenant_scope(bypass=True):
                await tenants.delete_by_id(tenant.id)
                if user is not None:
                    await User.objects.delete_by_id(user.id)
            raise

    @staticmethod
    async def _create_user(suffix: str, role_id: int) -> User:
        return await User.objects.create_user(
            username=f"p11t08-{suffix}",
            password=uuid4().hex,
            email=f"p11t08-{suffix}@example.invalid",
            role_id=role_id,
            is_active=True,
        )

    def actor_scope(self, user: User | None = None):
        return _actor_scope(self, user or self.users[0])

    async def add_actor(self) -> User:
        role = await PlatformRole.objects.filter(codename="user").first()
        owner_role = await WorkspaceRole.objects.filter(codename="owner").first()
        assert role is not None and owner_role is not None
        suffix = uuid4().hex
        user = await self._create_user(suffix, role.id)
        try:
            with tenant_scope(self.tenant.id):
                await tenant_users.create(
                    tenant_id=self.tenant.id,
                    channel="web",
                    external_user_id=str(user.id),
                    user_id=user.id,
                    role_id=owner_role.id,
                    ai_chat_enabled=True,
                    is_active=True,
                )
        except Exception:
            await User.objects.delete_by_id(user.id)
            raise
        self.users.append(user)
        return user

    async def scenario(self) -> AgentScenario:
        with self.actor_scope():
            scenario = await AgentScenario.objects.create(
                name=f"P11-T08 scenario {uuid4().hex[:10]}",
                analysis_types=["sentiment"],
                content_types=["text"],
                scope={},
                is_active=True,
            )
        self.scenario_ids.append(scenario.id)
        return scenario

    async def source(self) -> Source:
        platform = await Platform.objects.filter(platform_type="vk", is_active=True).first()
        assert platform is not None
        with self.actor_scope():
            source = await Source.objects.create(
                name=f"P11-T08 source {uuid4().hex[:10]}",
                platform_id=platform.id,
                external_id=f"p11t08-{uuid4().hex}",
                source_type=SourceType.GROUP,
                is_active=True,
            )
        self.source_ids.append(source.id)
        return source

    async def cleanup(self) -> None:
        with tenant_scope(bypass=True):
            tasks = await AgentTask.objects.filter(tenant_id=self.tenant.id)
            for task in reversed(tasks):
                await AgentTask.objects.delete_by_id(task.id)
            assert await AgentTask.objects.filter(tenant_id=self.tenant.id) == []
            assert await TaskBillingPayer.objects.filter(tenant_id=self.tenant.id).count() == 0
            jobs = await Job.objects.filter(tenant_id=self.tenant.id)
            for job in reversed(jobs):
                await Job.objects.delete_by_id(job.id)
            assert await Job.objects.filter(tenant_id=self.tenant.id) == []
            for scenario_id in reversed(self.scenario_ids):
                await AgentScenario.objects.delete_by_id(scenario_id)
            for source_id in reversed(self.source_ids):
                await Source.objects.delete_by_id(source_id)
            for user in self.users:
                await PersonalBillingAccount.objects.delete(user_id=user.id)
            await tenants.delete_by_id(self.tenant.id)
            for user in reversed(self.users):
                await User.objects.delete_by_id(user.id)


@contextmanager
def _actor_scope(workspace: AdmissionWorkspace, user: User):
    with tenant_scope(workspace.tenant.id), permission_scope(user, is_owner=True):
        yield


@pytest.fixture
async def admission_workspace(monkeypatch):
    # Consent is an append-only external side effect outside this quota contract;
    # mock it so the suite leaves no synthetic payer-consent records behind.
    consent = AsyncMock()
    monkeypatch.setattr("app.services.billing.payer.consent_current_user_as_task_payer", consent)
    workspace = await AdmissionWorkspace.create()
    try:
        yield workspace
    finally:
        await workspace.cleanup()


def _plan(scenario_id: int, source_id: int) -> dict:
    return {
        "goal": "P11-T08 admission probe",
        "platforms": ["vk"],
        "source_scope": "selected",
        "source_ids": [source_id],
        "scenario_id": scenario_id,
        "collection": {"content_types": ["posts"], "limit": 5, "queries": ["repair"]},
        "trigger": {"type": "once"},
    }


async def _prepare_receipt_task(
    workspace: AdmissionWorkspace,
    user: User,
    scenario: AgentScenario,
    source: Source,
    request_id: str,
) -> tuple[AgentTask, str]:
    raw_plan = _plan(scenario.id, source.id)
    parsed = parse_task_plan(raw_plan)
    canonical = task_plan_block(parsed)
    payload = apply_task_plan({}, canonical, job_type="analyze")
    if canonical.get("version", 1) == 1:
        payload["collect_before_analyze"] = True
    launch_key, fingerprint = launch_identity(
        workspace.tenant.id,
        user.id,
        request_id,
        canonical,
        "analyze",
    )
    with workspace.actor_scope(user):
        task = await AgentTask.objects.create(
            name=f"P11-T08 [{launch_key[:12]}]",
            cron_expr="@once",
            job_type="analyze",
            payload=payload,
            is_active=False,
            next_run_at=None,
            agent_scenario_id=scenario.id,
        )
        await AgentTask.objects.set_sources(task.id, [source.id])
        # Seed a trusted receipt identity through the ORM because public manager
        # methods intentionally reject caller-supplied launch identity fields.
        from sqlalchemy import update

        from app.core.database import async_session_maker

        async with async_session_maker() as session:
            async with session.begin():
                await session.execute(
                    update(AgentTask)
                    .where(AgentTask.id == task.id, AgentTask.tenant_id == workspace.tenant.id)
                    .values(launch_key=launch_key, launch_fingerprint=fingerprint)
                )
    return task, launch_key


async def _admit(workspace: AdmissionWorkspace, user: User, task: AgentTask, launch_key: str):
    with workspace.actor_scope(user):
        return await AgentTaskManager()._admit_task_run(
            task.id,
            now=datetime.now(timezone.utc),
            initiated_by_user_id=user.id,
            launch_request_key=launch_key,
        )


async def test_same_key_concurrent_launch_creates_one_task_and_job(
    admission_workspace: AdmissionWorkspace,
):
    workspace = admission_workspace
    scenario = await workspace.scenario()
    source = await workspace.source()
    actor = workspace.users[0]
    request_id = "p11t08-identical-race"
    plan = _plan(scenario.id, source.id)
    with workspace.actor_scope(actor):
        await configure_current_personal_account("20")
        first, second = await asyncio.gather(
            launch_social_task(plan, request_id=request_id),
            launch_social_task(plan, request_id=request_id),
        )

    assert {first["status"], second["status"]} == {"queued", "replayed"}
    assert first["task_id"] == second["task_id"]
    assert first["job_id"] == second["job_id"]
    with tenant_scope(workspace.tenant.id):
        task = await AgentTask.objects.get(id=first["task_id"])
        tasks = await AgentTask.objects.filter(launch_key=task.launch_key)
        jobs = await Job.objects.filter(agent_task_id=first["task_id"])
    assert len(tasks) == 1
    assert len(jobs) == 1
    assert jobs[0].id == first["job_id"]


async def test_actor_admission_serializes_distinct_requests_and_releases_terminal_capacity(
    admission_workspace: AdmissionWorkspace,
):
    workspace = admission_workspace
    scenario = await workspace.scenario()
    sources = [await workspace.source() for _ in range(4)]
    actor = workspace.users[0]
    request_ids = [f"p11t08-actor-{index:02}" for index in range(4)]
    plans = [_plan(scenario.id, source.id) for source in sources]

    with workspace.actor_scope(actor):
        await configure_current_personal_account("20")
        first = await launch_social_task(plans[0], request_id=request_ids[0])
        second = await launch_social_task(plans[1], request_id=request_ids[1])
        assert first["status"] == second["status"] == "queued"

        # Both different keys observe the same cap-1 state; the workspace gate
        # must serialize the insert/count transaction so only one reaches 3.
        contenders = await asyncio.gather(
            launch_social_task(plans[2], request_id=request_ids[2]),
            launch_social_task(plans[3], request_id=request_ids[3]),
            return_exceptions=True,
        )
        accepted = [result for result in contenders if isinstance(result, dict)]
        rejected = [(index, result) for index, result in enumerate(contenders) if isinstance(result, TaskLaunchError)]
        assert len(accepted) == 1
        assert len(rejected) == 1
        rejected_index, rejected_error = rejected[0]
        assert rejected_error.code == "actor_task_limit"
        assert "private" not in rejected_error.safe_message.lower()

        # Replaying an existing receipt while full remains safe and idempotent.
        replay = await launch_social_task(plans[0], request_id=request_ids[0])
        assert replay["status"] == "replayed"
        assert replay["task_id"] == first["task_id"]
        assert replay["job_id"] == first["job_id"]

        # Same key with changed payload remains a conflict while the actor is at
        # quota; quota policy must not turn a key collision into a new launch.
        with pytest.raises(TaskLaunchError) as conflict:
            await launch_social_task({**plans[0], "goal": "different intent"}, request_id=request_ids[0])
        assert conflict.value.code == "request_id_conflict"

    with tenant_scope(workspace.tenant.id):
        await Job.objects.update_by_id(first["job_id"], status="done", finished_at=datetime.now(timezone.utc))
    with workspace.actor_scope(actor):
        after_terminal = await launch_social_task(plans[2 + rejected_index], request_id=request_ids[2 + rejected_index])
    assert after_terminal["status"] == "queued"


async def test_workspace_admission_uses_locked_active_job_count_and_fails_closed(
    admission_workspace: AdmissionWorkspace,
    monkeypatch,
):
    workspace = admission_workspace
    scenario = await workspace.scenario()
    users = [workspace.users[0], await workspace.add_actor(), await workspace.add_actor()]
    sources = [await workspace.source() for _ in range(3)]
    receipt_tasks = [
        await _prepare_receipt_task(
            workspace,
            users[index],
            scenario,
            sources[index],
            f"p11t08-workspace-{index:02}",
        )
        for index in range(3)
    ]

    # Keep the real database counts, row lock, and job insertion. Only lower the
    # workspace's configured capacity so this boundary stays focused and small.
    original_limits = Tenant.effective_limits

    def constrained_limits(tenant: Tenant) -> dict:
        limits = original_limits(tenant)
        return {**limits, "max_tasks": 2} if tenant.id == workspace.tenant.id else limits

    monkeypatch.setattr(Tenant, "effective_limits", constrained_limits)
    for user in users:
        with workspace.actor_scope(user):
            await configure_current_personal_account("20")

    first = await _admit(workspace, users[0], *receipt_tasks[0])
    second = await _admit(workspace, users[1], *receipt_tasks[1])
    assert first["status"] == second["status"] == "enqueued"

    with pytest.raises(TaskLaunchError) as limited:
        await _admit(workspace, users[2], *receipt_tasks[2])
    assert limited.value.code == "workspace_admission_limit"

    with tenant_scope(workspace.tenant.id):
        active_jobs = await Job.objects.filter(status__in=("pending", "running"))
    assert len(active_jobs) == 2
    assert {job.initiated_by_user_id for job in active_jobs} == {users[0].id, users[1].id}
