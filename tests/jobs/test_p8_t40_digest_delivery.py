"""P8-T40 confirmed delivery acceptance on real PostgreSQL checkpoints.

Requires the approved root-conftest test_schema redirect. Snapshot creation,
checkpoint locking and ledger writes are real; only channel HTTP is mocked.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit
from uuid import uuid4

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
from test_p8_t29_social_launch import Workspace

from app.core.permissions import PermissionDeniedError, permission_scope
from app.core.tenant_context import tenant_scope
from app.models import AgentTask, DigestRun, Job, PlatformRole, TenantChannel, User, WorkspaceRole
from app.models.managers.job_manager import JobManager
from app.models.managers.tenant_manager import tenant_users
from app.services.task_plans import apply_task_plan
from app.types import ActionType

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


async def _cleanup_workspace_evidence(workspace: Workspace) -> None:
    """Delete NULL-task snapshots by job receipt before the test tenant is removed."""
    with tenant_scope(workspace.tenant.id):
        jobs = await Job.objects.filter(tenant_id=workspace.tenant.id)
        run_ids = set()
        for job in jobs:
            result = job.result if isinstance(job.result, dict) else {}
            receipt = result.get("task_digest_delivery")
            if isinstance(receipt, dict) and type(receipt.get("run_id")) is int:
                run_ids.add(receipt["run_id"])
        for run_id in run_ids:
            await DigestRun.objects.delete_by_id(run_id)
        assert await DigestRun.objects.filter(tenant_id=workspace.tenant.id) == []
        bindings = await TenantChannel.objects.filter(tenant_id=workspace.tenant.id)
        for binding in bindings:
            await TenantChannel.objects.delete_by_id(binding.id)
        assert await TenantChannel.objects.filter(tenant_id=workspace.tenant.id) == []


@pytest.fixture
async def delivery_workspace():
    workspace = await Workspace.create()
    workspace.extra_user_ids = []
    try:
        yield workspace
    finally:
        try:
            await _cleanup_workspace_evidence(workspace)
            await workspace.cleanup()
        finally:
            for user_id in workspace.extra_user_ids:
                await User.objects.delete_by_id(user_id)


def _plan(scenario_id: int, source_id: int) -> dict:
    return {
        "version": 2,
        "goal": "Collect, analyze and summarize evidence",
        "platforms": ["vk"],
        "source_scope": "selected",
        "source_ids": [source_id],
        "collection": {"content_types": ["posts"], "limit": 10, "queries": ["repair"]},
        "window": {"type": "rolling", "days": 3},
        "scenario_id": scenario_id,
        "trigger": {"type": "once"},
        "pipeline": {"stages": ["collect", "analyze", "digest"]},
        "digest": {
            "window": {"type": "rolling", "days": 7},
            "group_by": "themes",
            "time_breakdown": False,
        },
    }


async def _composed_run(workspace: Workspace, *, suffix: str = "run", text: str = "<b>Frozen report</b>"):
    scenario = await workspace.scenario()
    source = await workspace.source()
    plan = _plan(scenario.id, source.id)
    payload = apply_task_plan({}, plan, job_type="analyze")
    with workspace.actor_scope():
        task = await AgentTask.objects.create(
            name=f"P8-T40 {suffix} {uuid4().hex[:8]}",
            cron_expr="@once",
            job_type="analyze",
            payload=payload,
            is_active=False,
            agent_scenario_id=scenario.id,
        )
        workspace.task_ids.append(task.id)
        await AgentTask.objects.set_sources(task.id, [source.id])
        job = await JobManager().enqueue("analyze", payload={"task_plan": payload["task_plan"]}, agent_task_id=task.id)
        await Job.objects.update_by_id(
            job.id,
            status="done",
            result={
                "pipeline": {
                    "stages": {
                        "collect": {"state": "completed", "result": {"collected": 4}},
                        "analyze": {"state": "completed", "result": {"analyzed": 3}},
                        "digest": {
                            "state": "completed",
                            "result": {"text": text, "report_start": "2026-10-01", "report_end": "2026-10-07"},
                        },
                    }
                }
            },
        )
    return task, job


async def _target(workspace: Workspace) -> TenantChannel:
    with tenant_scope(workspace.tenant.id):
        return await TenantChannel.objects.create(
            tenant_id=workspace.tenant.id,
            channel="telegram",
            chat_id=f"p8t40-{workspace.tenant.id}-{uuid4().hex}",
            is_active=True,
            is_digest_target=True,
        )


def _channel(monkeypatch, send_part):
    from app.channels import registry

    channel = SimpleNamespace(send_part=AsyncMock(side_effect=send_part))
    monkeypatch.setattr(registry, "get_channel", lambda _name: channel)
    return channel


async def test_same_job_concurrent_send_is_locked_and_replay_sends_no_duplicate(delivery_workspace, monkeypatch):
    await _target(delivery_workspace)
    task, job = await _composed_run(delivery_workspace)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def send_part(*_args, **_kwargs):
        entered.set()
        await release.wait()
        return {"outcome": "sent", "message_id": "p8t40-message-1"}

    channel = _channel(monkeypatch, send_part)
    from app.services.digest.checkpoint_store import CheckpointBusy
    from app.services.task_digest_delivery import send_task_digest

    with delivery_workspace.actor_scope():
        first_task = asyncio.create_task(send_task_digest(task.id, job.id))
        await asyncio.wait_for(entered.wait(), timeout=5)
        with pytest.raises(CheckpointBusy):
            await send_task_digest(task.id, job.id)
        release.set()
        first = await first_task
        replay = await send_task_digest(task.id, job.id)

    assert first["status"] == replay["status"] == "sent"
    assert first["run_id"] == replay["run_id"]
    assert first["sent_parts"] == 1
    channel.send_part.assert_awaited_once()
    with tenant_scope(delivery_workspace.tenant.id):
        receipt_job = await Job.objects.get(id=job.id)
        run = await DigestRun.objects.get(id=first["run_id"])
    assert receipt_job.result["task_digest_delivery"] == {
        "run_id": run.id,
        "generation": run.delivery_state["generation"],
    }
    assert run.agent_task_id is None
    assert run.period_start == date(2026, 10, 1) and run.period_end == date(2026, 10, 7)


async def test_uncertain_transport_outcome_is_never_resent(delivery_workspace, monkeypatch):
    await _target(delivery_workspace)
    task, job = await _composed_run(delivery_workspace)

    async def timeout(*_args, **_kwargs):
        raise TimeoutError("mock transport timeout")

    channel = _channel(monkeypatch, timeout)
    from app.services.task_digest_delivery import send_task_digest

    with delivery_workspace.actor_scope():
        first = await send_task_digest(task.id, job.id)
        replay = await send_task_digest(task.id, job.id)
    assert first["status"] == replay["status"] == "uncertain"
    assert first["uncertain_parts"] == 1
    channel.send_part.assert_awaited_once()


async def test_revoked_binding_blocks_later_part_after_first_ack(delivery_workspace, monkeypatch):
    binding = await _target(delivery_workspace)
    long_report = "<b>Frozen report</b>" + (" evidence" * 1000)
    task, job = await _composed_run(delivery_workspace, text=long_report)
    calls = 0

    async def send_and_revoke(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        with delivery_workspace.actor_scope():
            await TenantChannel.objects.update_by_id(binding.id, is_active=False)
        return {"outcome": "sent", "message_id": "p8t40-first-part"}

    channel = _channel(monkeypatch, send_and_revoke)
    from app.services.task_digest_delivery import send_task_digest

    with delivery_workspace.actor_scope():
        receipt = await send_task_digest(task.id, job.id)
    assert receipt["status"] == "blocked"
    assert receipt["sent_parts"] == 1 and receipt["blocked_parts"] >= 1
    channel.send_part.assert_awaited_once()


async def test_foreign_task_job_and_denied_actor_never_send(delivery_workspace, monkeypatch):
    await _target(delivery_workspace)
    own_task, own_job = await _composed_run(delivery_workspace, suffix="own")
    foreign = await Workspace.create()
    try:
        foreign_task, foreign_job = await _composed_run(foreign, suffix="foreign")
        channel = _channel(monkeypatch, AsyncMock(return_value={"outcome": "sent", "message_id": "forbidden"}))
        from app.services.task_digest_delivery import send_task_digest

        # Both a foreign task/run pair and a same-workspace task bound to another
        # task's run fail before checkpoint creation or channel I/O.
        with delivery_workspace.actor_scope():
            with pytest.raises(ValueError, match="Retained task run unavailable"):
                await send_task_digest(foreign_task.id, foreign_job.id)
            other_task, other_job = await _composed_run(delivery_workspace, suffix="mismatch")
            with pytest.raises(ValueError, match="Retained task run unavailable"):
                await send_task_digest(own_task.id, other_job.id)

        platform_role = await PlatformRole.objects.filter(codename="user").first()
        viewer_role = await WorkspaceRole.objects.filter(codename="viewer").first()
        assert platform_role is not None and viewer_role is not None
        viewer = await User.objects.create_user(
            username=f"p8t40-viewer-{uuid4().hex}",
            password=uuid4().hex,
            email=f"p8t40-viewer-{uuid4().hex}@example.invalid",
            role_id=platform_role.id,
            is_active=True,
        )
        delivery_workspace.extra_user_ids.append(viewer.id)
        with tenant_scope(delivery_workspace.tenant.id):
            membership = await tenant_users.create(
                tenant_id=delivery_workspace.tenant.id,
                channel="web",
                external_user_id=str(viewer.id),
                user_id=viewer.id,
                role_id=viewer_role.id,
                ai_chat_enabled=True,
                is_active=True,
            )
            grants = await tenant_users.member_grants(membership)
        assert ("digestrun", ActionType.CREATE) not in grants
        with tenant_scope(delivery_workspace.tenant.id), permission_scope(viewer, member_grants=grants):
            with pytest.raises(PermissionDeniedError):
                await send_task_digest(own_task.id, own_job.id)
        channel.send_part.assert_not_awaited()
        with tenant_scope(delivery_workspace.tenant.id):
            assert await DigestRun.objects.filter(tenant_id=delivery_workspace.tenant.id) == []
    finally:
        try:
            await _cleanup_workspace_evidence(foreign)
        finally:
            await foreign.cleanup()
