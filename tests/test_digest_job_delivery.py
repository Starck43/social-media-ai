"""Isolated PostgreSQL integration cases. All HTTP/LLM boundaries are mocked.

These tests MUST run with tests/conftest.py's dedicated test database/schema.
They were prepared, not executed in the implementation sandbox.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import UniqueConstraint
from sqlalchemy.exc import IntegrityError

from app.core.database import async_session_maker
from app.core.tenant_context import tenant_scope
from app.models import DigestRun, Job, Tenant, TenantChannel
from app.models.managers.job_manager import JobManager
from app.services.digest import builder, job_delivery as module
from app.services.digest.delivery_outcomes import DeliveryFailure
from app.services.digest.snapshot_factory import create_snapshot_in_session

pytestmark = pytest.mark.tenancy
DAY = date(2026, 10, 9)


@pytest.fixture
async def workspace():
    tenant = await Tenant.objects.create(slug=f"job-delivery-{uuid4().hex}", name="Delivery", plan="business")
    try:
        yield tenant
    finally:
        await Tenant.objects.delete_by_id(tenant.id)


@pytest.fixture
def fake_build(monkeypatch):
    from app.services.digest import render
    aggregate = AsyncMock(return_value=({"brief": "frozen", "llm": {}}, DAY, DAY))
    summarize = AsyncMock(return_value=("summary", {"model": "mock", "cost": 0.12}))
    monkeypatch.setattr(builder, "period_bounds", lambda period: (DAY, DAY))
    monkeypatch.setattr(builder, "aggregate", aggregate)
    monkeypatch.setattr(builder, "_summarize", summarize)
    monkeypatch.setattr(render, "render_digest", lambda data, summary=None: "<b>Frozen report</b>")
    monkeypatch.setenv("DIGEST_CHECKPOINT_DELIVERY_ENABLED", "1")
    return SimpleNamespace(aggregate=aggregate, summarize=summarize)


async def target(tenant, destination="one"):
    # A destination label is test-local, not a globally shared messenger chat.
    chat_id = f"delivery-{tenant.id}-{destination}-{uuid4().hex}"
    with tenant_scope(tenant.id):
        return await TenantChannel.objects.create(
            tenant_id=tenant.id, channel="telegram", chat_id=chat_id,
            is_active=True, is_digest_target=True,
        )


async def claim(payload=None, task_id=None):
    row = await JobManager().enqueue("digest", payload=payload or {"period": "day"}, agent_task_id=task_id)
    return await JobManager.claim_job(row.id)


async def requeue(job):
    await Job.objects.update_by_id(job.id, status="pending", run_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    return await JobManager.claim_job(job.id)


async def execute(job):
    return await module.execute_digest_job(job, dict(job.payload or {}), AsyncMock(side_effect=AssertionError("legacy sender called")))


async def bound(job):
    row = await Job.objects.get(id=job.id)
    return row.result[module.REFERENCE_KEY]


def transport(monkeypatch, callback):
    from app.channels import registry
    channel = SimpleNamespace(send_part=AsyncMock(side_effect=callback))
    monkeypatch.setattr(registry, "get_channel", lambda name: channel)
    return channel


async def test_default_off_uses_legacy_without_a_new_snapshot(workspace, monkeypatch):
    monkeypatch.delenv("DIGEST_CHECKPOINT_DELIVERY_ENABLED", raising=False)
    with tenant_scope(workspace.id):
        job = await claim()
        legacy = AsyncMock(return_value={"status": "skipped"})
        assert await module.execute_digest_job(job, {}, legacy) == {"status": "skipped"}
        legacy.assert_awaited_once()
        assert await DigestRun.objects.filter() == []


async def test_job_binding_and_in_flight_intent_visible_before_http(workspace, fake_build, monkeypatch):
    await target(workspace)
    with tenant_scope(workspace.id):
        job = await claim({"period": "day", "job_id": 999999, "digest_run_id": 999999})

        async def send(chat_id, text, **kwargs):
            ref = await bound(job)
            run = await DigestRun.objects.get(id=ref["run_id"])
            assert ref["phase"] == "bound" and ref["generation"] == run.delivery_state["generation"]
            assert run.delivery_state["targets"][0]["parts"][0]["status"] == "in_flight"
            return {"success": True, "outcome": "sent", "message_id": "receipt-1"}

        channel = transport(monkeypatch, send)
        result = await execute(job)
        assert result["status"] == "sent" and result["sent_parts"] == 1
        assert await module.finalize_digest_job(job, result=result) is False
        finished = await Job.objects.get(id=job.id)
        run = await DigestRun.objects.get(id=result["run_id"])
        assert finished.status == "done" and finished.llm_cost is None and run.llm_cost == 0.12
        assert module.REFERENCE_KEY in finished.result
        channel.send_part.assert_awaited_once()


async def test_two_targets_partial_restart_after_midnight_has_no_rebuild_or_duplicate(workspace, fake_build, monkeypatch):
    from app.services.digest import render
    monkeypatch.setattr(render, "render_digest", lambda data, summary=None: "<b>" + "word " * 1000 + "</b>")
    first_target = await target(workspace, "one")
    second_target = await target(workspace, "two")
    calls = []
    rejected = False

    async def send(destination, text, **kwargs):
        nonlocal rejected
        calls.append((destination, text))
        if destination == first_target.chat_id and sum(d == first_target.chat_id for d, _ in calls) == 2 and not rejected:
            rejected = True
            return {"success": False, "outcome": "rejected", "error_code": "http_400"}
        return {"success": True, "outcome": "sent", "message_id": str(len(calls))}

    transport(monkeypatch, send)
    with tenant_scope(workspace.id):
        job = await claim()
        with pytest.raises(DeliveryFailure) as failure:
            await execute(job)
        assert failure.value.result["status"] == "partial" and failure.value.retryable
        ref = await bound(job)
        first_run = await DigestRun.objects.get(id=ref["run_id"])
        assert await module.finalize_digest_job(job, failure=failure.value) is True
        monkeypatch.setattr(builder, "period_bounds", lambda period: (DAY + timedelta(days=1), DAY + timedelta(days=1)))
        retry = await JobManager.claim_job(job.id, now=datetime.now(timezone.utc) + timedelta(days=1))
        result = await execute(retry)
        assert result["status"] == "sent" and await bound(retry) == ref
        assert len(calls) == 5  # 4 first-attempt requests, only rejected part retried
        assert sum(d == second_target.chat_id for d, _ in calls) == 2
        assert sum(d == first_target.chat_id and text == calls[0][1] for d, text in calls) == 1
        run = await DigestRun.objects.get(id=ref["run_id"])
        assert run.content == first_run.content and run.period_start == DAY and run.llm_cost == 0.12
        fake_build.aggregate.assert_awaited_once()
        fake_build.summarize.assert_awaited_once()


async def test_uncertain_outcome_and_cancelled_in_flight_never_replay(workspace, fake_build, monkeypatch):
    await target(workspace)
    async def send(*args, **kwargs):
        raise asyncio.CancelledError()
    channel = transport(monkeypatch, send)
    with tenant_scope(workspace.id):
        job = await claim()
        with pytest.raises(asyncio.CancelledError):
            await execute(job)
        ref = await bound(job)
        retry = await requeue(job)
        with pytest.raises(DeliveryFailure) as failure:
            await execute(retry)
        assert failure.value.result["status"] == "uncertain" and not failure.value.retryable
        assert await bound(retry) == ref
        channel.send_part.assert_awaited_once()
        fake_build.summarize.assert_awaited_once()


async def test_revoked_target_blocks_without_http_or_success(workspace, fake_build, monkeypatch):
    binding = await target(workspace)
    original = module._validate_bound
    async def revoke(job, ref):
        await original(job, ref)
        await TenantChannel.objects.update_by_id(binding.id, is_active=False)
    monkeypatch.setattr(module, "_validate_bound", revoke)
    channel = transport(monkeypatch, AsyncMock())
    with tenant_scope(workspace.id):
        job = await claim()
        with pytest.raises(DeliveryFailure) as failure:
            await execute(job)
        assert failure.value.result["status"] == "blocked" and not failure.value.retryable
        await module.finalize_digest_job(job, failure=failure.value)
        assert (await Job.objects.get(id=job.id)).status == "failed"
        channel.send_part.assert_not_awaited()


async def test_snapshot_and_reference_rollback_together_and_summary_is_not_rebilled(workspace, fake_build, monkeypatch):
    from app.services.digest import snapshot_factory
    await target(workspace)
    def fail(**kwargs):
        raise RuntimeError("mock checkpoint construction failure")
    monkeypatch.setattr(snapshot_factory, "new_checkpoint", fail)
    with tenant_scope(workspace.id):
        job = await claim()
        with pytest.raises(DeliveryFailure):
            await execute(job)
        assert await DigestRun.objects.filter() == []
        row = await Job.objects.get(id=job.id)
        assert row.result[module.REFERENCE_KEY]["phase"] == "reserved"
        assert row.result[module.REFERENCE_KEY]["build_started"] is True and row.llm_cost == 0.12
        retry = await requeue(job)
        with pytest.raises(DeliveryFailure, match="summary_build_requires_reconciliation"):
            await execute(retry)
        fake_build.summarize.assert_awaited_once()


async def test_transaction_helper_does_not_commit_an_unbound_snapshot(workspace):
    await target(workspace)
    with tenant_scope(workspace.id):
        async with async_session_maker() as session:
            with pytest.raises(RuntimeError):
                async with session.begin():
                    ref = await create_snapshot_in_session(
                        session, content="<b>Atomic</b>", period="day", period_start=DAY, period_end=DAY,
                    )
                    assert await DigestRun.objects.get(id=ref.run_id) is None
                    raise RuntimeError("caller binding failed")
        assert await DigestRun.objects.filter() == []


async def test_reserved_date_change_fails_before_model(workspace, fake_build):
    with tenant_scope(workspace.id):
        job = await claim()
        request = await module._request(job, job.payload)
        await module._reserve(job, request)
        fake_build.aggregate.return_value = ({"brief": "next day"}, DAY + timedelta(days=1), DAY + timedelta(days=1))
        with pytest.raises(DeliveryFailure, match="reserved_window_changed_before_summary"):
            await execute(job)
        fake_build.summarize.assert_not_awaited()
        assert await DigestRun.objects.filter() == []


async def test_old_retry_without_reference_cannot_guess_an_unsent_generation(workspace, fake_build):
    with tenant_scope(workspace.id):
        job = await claim()
        retry = await requeue(job)
        with pytest.raises(DeliveryFailure, match="legacy_retry_has_no_original_reference"):
            await execute(retry)
        fake_build.aggregate.assert_not_awaited()
        fake_build.summarize.assert_not_awaited()


async def test_claim_loss_cannot_complete_or_fail_new_owner(workspace, fake_build):
    with tenant_scope(workspace.id):
        job = await claim()
        await Job.objects.update_by_id(job.id, attempts=job.attempts + 1)
        with pytest.raises(DeliveryFailure, match="job_claim_lost"):
            await execute(job)
        assert await module.finalize_digest_job(job, failure=DeliveryFailure("test_failure")) is None
        assert (await Job.objects.get(id=job.id)).status == "running"
        fake_build.summarize.assert_not_awaited()


async def test_disabled_rollout_never_falls_back_for_a_bound_job(workspace, fake_build, monkeypatch):
    await target(workspace)
    transport(monkeypatch, AsyncMock(return_value={"outcome": "sent", "message_id": "ok"}))
    with tenant_scope(workspace.id):
        job = await claim()
        await execute(job)
        updated = await Job.objects.get(id=job.id)
        legacy = AsyncMock()
        monkeypatch.delenv("DIGEST_CHECKPOINT_DELIVERY_ENABLED", raising=False)
        with pytest.raises(DeliveryFailure, match="disabled_no_legacy_fallback"):
            await module.execute_digest_job(updated, {}, legacy)
        legacy.assert_not_awaited()


async def test_rate_limit_without_retry_after_requires_operator_delay(workspace, fake_build, monkeypatch):
    await target(workspace, "one")
    await target(workspace, "two")
    channel = transport(monkeypatch, AsyncMock(return_value={"outcome": "rejected", "error_code": "http_429"}))
    with tenant_scope(workspace.id):
        job = await claim()
        with pytest.raises(DeliveryFailure) as failure:
            await execute(job)
        assert not failure.value.retryable
        assert failure.value.result["reason"] == "rate_limit_requires_operator_delay"
        assert failure.value.result["pending_parts"] == 1
        channel.send_part.assert_awaited_once()


async def test_foreign_run_reference_cannot_authorize_http(workspace, fake_build, monkeypatch):
    other = await Tenant.objects.create(slug=f"foreign-delivery-{uuid4().hex}", name="Other", plan="business")
    channel = transport(monkeypatch, AsyncMock(return_value={"outcome": "sent", "message_id": "ok"}))
    try:
        own_target = await target(workspace)
        foreign_target = await target(other)
        assert own_target.chat_id != foreign_target.chat_id
        with tenant_scope(other.id):
            foreign_job = await claim()
            await execute(foreign_job)
            foreign_ref = await bound(foreign_job)
        with tenant_scope(workspace.id):
            job = await claim()
            await execute(job)
            row = await Job.objects.get(id=job.id)
            forged = {**row.result[module.REFERENCE_KEY], "run_id": foreign_ref["run_id"], "generation": foreign_ref["generation"]}
            await Job.objects.update_by_id(job.id, result={**row.result, module.REFERENCE_KEY: forged})
            channel.send_part.reset_mock()
            retry = await requeue(job)
            with pytest.raises(DeliveryFailure, match="original_run_mismatch"):
                await execute(retry)
            channel.send_part.assert_not_awaited()
    finally:
        await Tenant.objects.delete_by_id(other.id)


async def test_force_cannot_overwrite_or_create_a_new_generation(workspace, fake_build):
    with tenant_scope(workspace.id):
        job = await claim({"period": "day", "force_refresh": True})
        with pytest.raises(DeliveryFailure, match="force_requires_reviewed_new_generation"):
            await execute(job)
        fake_build.summarize.assert_not_awaited()
        assert await DigestRun.objects.filter() == []


async def test_same_schedule_jobs_share_build_lock_before_any_llm(workspace, fake_build):
    from app.models import AgentTask
    with tenant_scope(workspace.id):
        task = await AgentTask.objects.create(name="serialized", job_type="digest", cron_expr="0 9 * * *")
        first, second = await claim(task_id=task.id), await claim(task_id=task.id)
        async with module._job_lock(first):
            with pytest.raises(DeliveryFailure, match="digest_job_busy"):
                async with module._job_lock(second):
                    pytest.fail("same schedule acquired two build locks")
        fake_build.summarize.assert_not_awaited()


async def test_unknown_summary_cost_is_not_reported_as_free(workspace, fake_build, monkeypatch):
    await target(workspace)
    fake_build.summarize.return_value = ("summary", {"model": "mock", "cost": None})
    transport(monkeypatch, AsyncMock(return_value={"outcome": "sent", "message_id": "ok"}))
    with tenant_scope(workspace.id):
        job = await claim()
        result = await execute(job)
        row = await Job.objects.get(id=job.id)
        assert row.result["digest_build_cost_known"] is False
        assert (await DigestRun.objects.get(id=result["run_id"])).llm_cost is None


def test_tenant_channel_constraint_keeps_global_chat_ownership():
    constraint = next(
        item for item in TenantChannel.__table__.constraints
        if isinstance(item, UniqueConstraint) and item.name == "uq_tenant_channel_chat"
    )
    assert tuple(column.name for column in constraint.columns) == ("channel", "chat_id")


async def test_database_rejects_same_chat_bound_to_two_workspaces(workspace):
    other = await Tenant.objects.create(slug=f"duplicate-chat-{uuid4().hex}", name="Other", plan="business")
    try:
        original = await target(workspace)
        with pytest.raises(IntegrityError) as failure:
            async with async_session_maker() as session:
                async with session.begin():
                    session.add(TenantChannel(
                        tenant_id=other.id, channel=original.channel, chat_id=original.chat_id,
                        is_active=True, is_digest_target=True,
                    ))
                    await session.flush()
        assert "uq_tenant_channel_chat" in str(failure.value.orig)
        with tenant_scope(workspace.id):
            assert (await TenantChannel.objects.get(id=original.id)).tenant_id == workspace.id
        with tenant_scope(other.id):
            assert await TenantChannel.objects.filter(tenant_id=other.id, chat_id=original.chat_id) == []
    finally:
        await Tenant.objects.delete_by_id(other.id)
