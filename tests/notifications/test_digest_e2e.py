"""End-to-end M2 acceptance: cron schedule → job → digest → channel.

Uses the real task runner, queue and digest builder against the real database;
only the LLM summary and the outbound channel HTTP call are stubbed.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.core.tenant_context import tenant_scope
from app.jobs import dispatcher
from app.jobs.handlers import handle_digest
from app.jobs.recovery_policy import outcome_unconfirmed
from app.models import AgentTask, DigestRun, Job
from app.services.digest import builder
from app.tasks import runner

SCHEDULE_NAME = "it-e2e-digest"
ONCE_NAME = "it-e2e-once"
OWNED_NAMES = (SCHEDULE_NAME, ONCE_NAME)


@pytest.fixture(autouse=True)
async def _bootstrap_workspace():
    """Enter the bootstrap workspace for the runtime's inline actor resolution.

    Its owner preset left no seeded web member, so the suite acts as its own
    synthetic owner (created in `_billing_payer`) and only the ambient scope here
    names the workspace the digest reads.
    """
    from app.core.config import settings
    from app.models import Tenant

    tenant = await Tenant.objects.get(slug=settings.DEFAULT_TENANT_SLUG)
    with tenant_scope(tenant.id):
        yield tenant


@pytest.fixture(autouse=True)
async def _bound_digest_recipient(_bootstrap_workspace):
    """Legacy env addresses alone are not authorization to deliver."""
    from app.models import TenantChannel

    binding = await TenantChannel.objects.create(
        tenant_id=_bootstrap_workspace.id,
        channel="telegram",
        chat_id="@chan",
        is_active=True,
        is_digest_target=True,
    )
    try:
        yield
    finally:
        await TenantChannel.objects.delete_by_id(binding.id)


@pytest.fixture(autouse=True)
async def _cleanup(_bootstrap_workspace):
    """Own this suite's rows, workspace-scoped, before and after every test.

    Task deletion goes through the real admission lock, which refuses to run in an
    ambient context that does not match the row's workspace, so the scope is set
    explicitly here instead of bypassed.
    """
    tenant = _bootstrap_workspace

    async def wipe():
        with tenant_scope(tenant.id):
            await DigestRun.objects.delete(tenant_id=tenant.id)
            await Job.objects.delete(job_type="digest", tenant_id=tenant.id)
            for name in OWNED_NAMES:
                await AgentTask.objects.delete(name=name, tenant_id=tenant.id)

    # The suite runs against the real database, so before each test we park
    # every foreign schedule and clear undrained jobs: otherwise a cron task
    # that came due while the runtime is down gets counted by runner.tick()
    # (stats["enqueued"] == 2) and its job is actually executed by drain().
    with tenant_scope(tenant.id):
        foreign = [t.id for t in await AgentTask.objects.filter(tenant_id=tenant.id) if t.name not in OWNED_NAMES]
        for task_id in foreign:
            await AgentTask.objects.update_by_id(task_id, is_active=False)
        await Job.objects.delete(status="pending")

    await wipe()
    try:
        yield
    finally:
        await wipe()
        with tenant_scope(tenant.id):
            for task_id in foreign:
                await AgentTask.objects.update_by_id(task_id, is_active=True)


@pytest.fixture(autouse=True)
async def _billing_payer(_bootstrap_workspace, monkeypatch):
    """A dispatched digest resolves its stored payer before any model call.

    `task_billing_payers` and `personal_billing_accounts` are append-only by DB
    guard, so this fixture never writes either. It owns one synthetic owner (with
    a workspace membership, so the doubled payer names a real member of the
    workspace) and doubles the stored-payer resolution the worker consults; the
    "no payer stored" failure path is covered by the billing suite, not here.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from app.models import PlatformRole, TenantUser, User, WorkspaceRole

    tenant = _bootstrap_workspace
    tag = "it-digest-e2e-" + uuid4().hex[:12]
    role = await WorkspaceRole.objects.get(codename="owner")
    platform_role = await PlatformRole.objects.get(codename="user")
    owner = await User.objects.create(
        username=tag,
        email=f"{tag}@example.invalid",
        hashed_password="synthetic-not-a-password",
        role_id=platform_role.id,
        is_active=True,
    )
    membership = None
    try:
        with tenant_scope(tenant.id):
            membership = await TenantUser.objects.create(
                tenant_id=tenant.id,
                user_id=owner.id,
                channel="web",
                external_user_id=str(owner.id),
                role_id=role.id,
                is_active=True,
            )
        with tenant_scope(tenant.id):
            account = SimpleNamespace(user_id=owner.id, is_active=True, configured_at=None)
        resolve = AsyncMock(return_value=account)
        monkeypatch.setattr("app.services.billing.payer.resolve_scheduled_task_payer", resolve)
        # A legacy consent write during a run would break the append-only guard.
        monkeypatch.setattr("app.services.billing.payer.consent_current_user_as_task_payer", AsyncMock())
        yield owner
    finally:
        if membership is not None:
            with tenant_scope(tenant.id):
                await TenantUser.objects.delete_by_id(membership.id)
        await User.objects.delete_by_id(owner.id)


@pytest.fixture
async def make_schedule(_billing_payer):
    """Create an owned task whose stored payer the worker path resolves.

    A real consent cannot be recorded here: `task_billing_payers` and
    `personal_billing_accounts` are append-only by DB guard, and the consent service
    refuses a caller without a configured daily limit before it reaches that write.
    `_billing_payer` therefore doubles the two worker-side reads (stored payer
    resolution and the legacy consent wrapper) instead of inserting either row.
    """

    async def create(name=SCHEDULE_NAME, **fields):
        return await AgentTask.objects.create(name=name, **fields)

    return create


async def test_dispatcher_passes_agent_task_id_to_handler(monkeypatch, make_schedule):
    """Job context in columns (agent_task_id) must reach the handler payload."""
    seen = {}

    async def fake_handler(payload):
        seen.update(payload)
        return {"ok": True}

    monkeypatch.setitem(dispatcher.HANDLERS, "digest", fake_handler)
    schedule = await make_schedule(
        name=SCHEDULE_NAME,
        cron_expr="0 9 * * *",
        job_type="digest",
        payload={"period": "week"},
        is_active=True,
    )
    await dispatcher.jobs.enqueue(job_type="digest", payload=schedule.payload, agent_task_id=schedule.id)

    assert await dispatcher.drain() == 1
    assert seen["agent_task_id"] == schedule.id
    assert seen["period"] == "week"
    assert isinstance(seen["job_id"], int)


async def test_schedule_to_channel_end_to_end(monkeypatch, make_schedule):
    """A due schedule produces exactly one digest delivery in its channel."""
    from app.channels import registry as registry_module

    sent: list[str] = []

    class StubChannel:
        async def send(self, chat_id, text, parse_mode=None):
            sent.append(text)
            return {"success": True, "message_id": 1234}

    async def fake_summarize(data):
        return "Тестовая сводка", {"model": "test-model"}

    monkeypatch.setattr(registry_module, "get_channel", lambda name: StubChannel())
    monkeypatch.setattr(registry_module.settings, "TELEGRAM_DIGEST_CHANNEL_ID", "@chan")
    monkeypatch.setattr(registry_module.settings, "MAX_CHANNEL_ID", "")
    monkeypatch.setattr(builder, "_summarize", fake_summarize)

    due = datetime.now(timezone.utc) - timedelta(minutes=1)
    schedule = await make_schedule(
        name=SCHEDULE_NAME,
        cron_expr="0 9 * * *",
        job_type="digest",
        payload={"period": "day"},
        is_active=True,
        next_run_at=due,
    )

    # Runner pass enqueues the job and advances the schedule. Enqueueing is not
    # a run result, so the task reads "queued" until the worker reports back.
    stats = await runner.tick()
    assert stats["enqueued"] == 1

    refreshed = await AgentTask.objects.get(id=schedule.id)
    assert refreshed.next_run_at > due
    assert refreshed.last_status == "queued"

    # Worker drains the queue → digest built and delivered
    assert await dispatcher.drain() == 1
    assert len(sent) == 1 and "Тестовая сводка" in sent[0]
    assert (await AgentTask.objects.get(id=schedule.id)).last_status == "ok"

    run = await DigestRun.objects.get(agent_task_id=schedule.id)
    assert run.status == "sent" and run.message_id == "1234"

    # Rows come back without an ORDER BY, so pick jobs by id, never by position.
    (job,) = await Job.objects.filter(job_type="digest")
    assert job.status == "done"

    # Re-running the same period is a no-op (idempotent), and a fresh job
    # for it does not deliver a second message.
    again = await dispatcher.jobs.enqueue(job_type="digest", payload={"period": "day"}, agent_task_id=schedule.id)
    assert again.id != job.id
    assert await dispatcher.drain() == 1
    assert len(sent) == 1

    skipped = await Job.objects.get(id=again.id)
    assert skipped.status == "done" and skipped.result["reason"] == "already_sent"


async def test_handler_rejects_unknown_period(monkeypatch):
    result = await handle_digest({"period": "hour"})
    assert result["status"] == "failed" and "hour" in result["error"]


async def test_failed_delivery_stops_for_review_and_reattempt_reuses_run_row(monkeypatch, make_schedule):
    """A delivery nobody received is not retried blindly; a new attempt reuses the run row.

    The legacy path cannot tell a rejection from a lost acknowledgement, so the
    dispatcher records a terminal "outcome unconfirmed" failure instead of an
    automatic retry. A later attempt for the same (schedule, period) — an
    operator decision — must reuse the digest_runs row (unique constraint) and
    actually deliver.
    """
    from app.channels import registry as registry_module

    attempts: list[str] = []

    class FlakyChannel:
        async def send(self, chat_id, text, parse_mode=None):
            attempts.append(text)
            if len(attempts) == 1:
                return {"success": False, "error": "temporary upstream failure"}
            return {"success": True, "message_id": 999}

    async def fake_summarize(data):
        return "Сводка", {"model": "test-model"}

    monkeypatch.setattr(registry_module, "get_channel", lambda name: FlakyChannel())
    monkeypatch.setattr(registry_module.settings, "TELEGRAM_DIGEST_CHANNEL_ID", "@chan")
    monkeypatch.setattr(registry_module.settings, "MAX_CHANNEL_ID", "")
    monkeypatch.setattr(builder, "_summarize", fake_summarize)

    schedule = await make_schedule(
        name=SCHEDULE_NAME,
        cron_expr="0 9 * * *",
        job_type="digest",
        payload={"period": "day"},
        is_active=True,
    )

    await dispatcher.jobs.enqueue(job_type="digest", payload={"period": "day"}, agent_task_id=schedule.id)
    assert await dispatcher.drain() == 1
    assert len(attempts) == 1

    (failed,) = await Job.objects.filter(job_type="digest")
    assert failed.status == "failed" and outcome_unconfirmed(failed.error)
    assert failed.attempts == 1
    assert (await AgentTask.objects.get(id=schedule.id)).last_status == "failed"

    run = await DigestRun.objects.get(agent_task_id=schedule.id)
    assert run.status == "failed"

    # No automatic retry: nothing is left to drain and nothing else went out.
    assert await dispatcher.drain() == 0
    assert len(attempts) == 1

    # Explicit re-attempt for the same period: a new job, the same run row.
    await dispatcher.jobs.enqueue(job_type="digest", payload={"period": "day"}, agent_task_id=schedule.id)
    assert await dispatcher.drain() == 1

    assert len(attempts) == 2  # second attempt really went out
    retried = max(await Job.objects.filter(job_type="digest"), key=lambda job: job.id)
    assert retried.id != failed.id and retried.status == "done"
    assert (await Job.objects.get(id=failed.id)).status == "failed"

    runs = await DigestRun.objects.filter(agent_task_id=schedule.id)
    assert len(runs) == 1 and runs[0].status == "sent"


async def test_once_task_is_deactivated_after_trigger(make_schedule):
    """A @once schedule enqueues once and is deactivated, not rescheduled."""
    due = datetime.now(timezone.utc) - timedelta(minutes=1)
    schedule = await make_schedule(
        name=ONCE_NAME,
        cron_expr="@once",
        job_type="digest",
        payload={"period": "day"},
        is_active=True,
        next_run_at=due,
    )

    stats = await runner.tick()
    assert stats["enqueued"] == 1

    refreshed = await AgentTask.objects.get(id=schedule.id)
    assert refreshed.is_active is False
    assert refreshed.next_run_at is None
    assert refreshed.last_status == "queued"

    # A second tick must not enqueue again (task is inactive, no next run)
    stats2 = await runner.tick()
    assert stats2["enqueued"] == 0
