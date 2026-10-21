"""Lease loss during a legacy scheduled digest leaves reviewable evidence, never a resend.

Real queue, dispatcher heartbeat, stale-lease reaper and digest builder against the
test database; only the LLM summary and the outbound channel call are stubbed. The
worker loses its claim while the channel send is in flight, so delivery is unknown.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.jobs import dispatcher
from app.jobs.claim_outcomes import JobClaimLostError
from app.jobs.recovery_policy import outcome_unconfirmed
from app.models import AgentTask, DigestRun, Job
from app.models.managers.digest_run_manager import DIGEST_RUN_IN_PROGRESS, FORCED_TAKEOVER_AFTER
from app.services.digest import builder

SCHEDULE_NAME = "it-digest-lease"


@pytest.fixture(autouse=True)
async def _cleanup():
    async def wipe():
        await DigestRun.objects.delete()
        await Job.objects.delete(job_type="digest")
        await AgentTask.objects.delete(name=SCHEDULE_NAME)

    # Only this test's jobs may run: park foreign schedules and undrained jobs.
    foreign = [t.id for t in await AgentTask.objects.all() if t.name != SCHEDULE_NAME and t.is_active]
    for task_id in foreign:
        await AgentTask.objects.update_by_id(task_id, is_active=False)
    await Job.objects.delete(status="pending")
    await wipe()
    try:
        yield
    finally:
        await wipe()
        for task_id in foreign:
            await AgentTask.objects.update_by_id(task_id, is_active=True)


@pytest.fixture(autouse=True)
async def _bound_digest_recipient(_cleanup):
    from app.core.config import settings
    from app.models import Tenant, TenantChannel

    tenant = await Tenant.objects.get(slug=settings.DEFAULT_TENANT_SLUG)
    binding = await TenantChannel.objects.create(
        tenant_id=tenant.id, channel="telegram", chat_id="@chan", is_active=True, is_digest_target=True
    )
    try:
        yield
    finally:
        await TenantChannel.objects.delete_by_id(binding.id)


async def test_lease_loss_mid_send_stops_for_review_without_resend(monkeypatch):
    from app.channels import registry as registry_module
    from app.core.tenant_context import tenant_scope

    sends, summaries = [], []
    reaped = asyncio.Event()

    class StubChannel:
        async def send(self, chat_id, text, parse_mode=None):
            sends.append(text)
            if len(sends) == 1:
                # The worker stalls mid-send; another worker's reaper declares the lease stale.
                (running,) = await Job.objects.filter(job_type="digest", status="running")
                stale = datetime.now(timezone.utc) - timedelta(minutes=31)
                assert await Job.objects.filter(id=running.id).update(locked_at=stale) == 1
                with tenant_scope(bypass=True):
                    assert await dispatcher.jobs.reap_stale() == 1
                reaped.set()
                await asyncio.Event().wait()  # never returns: the send outcome stays unknown
            return {"success": True, "message_id": 77}

    async def fake_summarize(data):
        summaries.append(data)
        return "Сводка", {"model": "test-model"}

    real_renew = dispatcher.jobs.renew_claim
    renewals = []

    async def gated_renew(claim, now=None):
        renewals.append(claim.job_id)
        if len(renewals) > 1:  # heartbeat renewals wait until the reaper has taken the row
            await reaped.wait()
        return await real_renew(claim, now)

    monkeypatch.setattr(registry_module, "get_channel", lambda name: StubChannel())
    monkeypatch.setattr(registry_module.settings, "TELEGRAM_DIGEST_CHANNEL_ID", "@chan")
    monkeypatch.setattr(registry_module.settings, "MAX_CHANNEL_ID", "")
    monkeypatch.setattr(builder, "_summarize", fake_summarize)
    monkeypatch.setattr(dispatcher, "_ORDINARY_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(dispatcher.jobs, "renew_claim", gated_renew)

    schedule = await AgentTask.objects.create(
        name=SCHEDULE_NAME, cron_expr="0 9 * * *", job_type="digest", payload={"period": "day"}, is_active=True
    )
    first = await dispatcher.jobs.enqueue(job_type="digest", payload={"period": "day"}, agent_task_id=schedule.id)

    # The heartbeat sees the lost claim and stops the handler; the worker does not
    # turn that into a handler failure or an automatic replay.
    with pytest.raises(JobClaimLostError):
        await asyncio.wait_for(dispatcher.drain(), timeout=10)
    assert len(sends) == 1 and len(summaries) == 1

    lost = await Job.objects.get(id=first.id)
    assert lost.status == "failed" and outcome_unconfirmed(lost.error)
    run = await DigestRun.objects.get(agent_task_id=schedule.id)
    assert run.status == "pending" and run.message_id is None  # delivery unknown, kept as evidence

    # Nothing is requeued, and an explicit scheduled re-attempt stops before cost or send.
    assert await dispatcher.drain() == 0
    retry = await dispatcher.jobs.enqueue(job_type="digest", payload={"period": "day"}, agent_task_id=schedule.id)
    assert await dispatcher.drain() == 1
    stopped = await Job.objects.get(id=retry.id)
    assert stopped.status == "failed" and stopped.error == DIGEST_RUN_IN_PROGRESS  # visible to the operator
    assert len(sends) == 1 and len(summaries) == 1

    # So does an early forced resend; after the takeover window it is the operator's decision.
    with tenant_scope(schedule.tenant_id):
        early = await builder.build_and_publish(period="day", agent_task_id=schedule.id, force=True)
        assert early == {"status": "failed", "error": DIGEST_RUN_IN_PROGRESS}
        stale_at = datetime.now(timezone.utc) - FORCED_TAKEOVER_AFTER - timedelta(minutes=1)
        assert await DigestRun.objects.filter(id=run.id).update(updated_at=stale_at) == 1
        forced = await builder.build_and_publish(period="day", agent_task_id=schedule.id, force=True)
    assert forced["status"] == "sent" and len(sends) == 2 and len(summaries) == 2
    runs = await DigestRun.objects.filter(agent_task_id=schedule.id)
    assert len(runs) == 1 and runs[0].id == run.id and runs[0].status == "sent"
