"""End-to-end M2 acceptance: cron schedule → job → digest → channel.

Uses the real scheduler, queue and digest builder against the real database;
only the LLM summary and the outbound channel HTTP call are stubbed.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.jobs import dispatcher
from app.jobs.handlers import handle_digest
from app.models import DigestRun, Job, Schedule
from app.scheduler import runner
from app.services.digest import builder

SCHEDULE_NAME = "it-e2e-digest"


@pytest.fixture(autouse=True)
async def _cleanup():
    async def wipe():
        await DigestRun.objects.delete()
        await Job.objects.delete(job_type="digest")
        await Schedule.objects.delete(name=SCHEDULE_NAME)

    await wipe()
    yield
    await wipe()


async def test_dispatcher_passes_schedule_id_to_handler(monkeypatch):
    """Job context in columns (schedule_id) must reach the handler payload."""
    seen = {}

    async def fake_handler(payload):
        seen.update(payload)
        return {"ok": True}

    monkeypatch.setitem(dispatcher.HANDLERS, "digest", fake_handler)

    schedule = await Schedule.objects.create(
        name=SCHEDULE_NAME,
        cron_expr="0 9 * * *",
        timezone="UTC",
        job_type="digest",
        payload={"period": "week"},
        is_active=True,
    )
    await dispatcher.jobs.enqueue(job_type="digest", payload=schedule.payload, schedule_id=schedule.id)

    assert await dispatcher.drain() == 1
    assert seen["schedule_id"] == schedule.id
    assert seen["period"] == "week"
    assert isinstance(seen["job_id"], int)


async def test_schedule_to_channel_end_to_end(monkeypatch):
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
    schedule = await Schedule.objects.create(
        name=SCHEDULE_NAME,
        cron_expr="0 9 * * *",
        timezone="UTC",
        job_type="digest",
        payload={"period": "day"},
        is_active=True,
        next_run_at=due,
    )

    # Scheduler pass enqueues the job and advances the schedule
    stats = await runner.tick()
    assert stats["enqueued"] == 1

    refreshed = await Schedule.objects.get(id=schedule.id)
    assert refreshed.next_run_at > due
    assert refreshed.last_status == "ok"

    # Worker drains the queue → digest built and delivered
    assert await dispatcher.drain() == 1
    assert len(sent) == 1 and "Тестовая сводка" in sent[0]

    run = await DigestRun.objects.get(schedule_id=schedule.id)
    assert run.status == "sent" and run.message_id == "1234"

    job = (await Job.objects.filter(job_type="digest"))[0]
    assert job.status == "done"

    # Re-running the same period is a no-op (idempotent), and a fresh job
    # for it does not deliver a second message.
    await dispatcher.jobs.enqueue(job_type="digest", payload={"period": "day"}, schedule_id=schedule.id)
    assert await dispatcher.drain() == 1
    assert len(sent) == 1

    skipped = (await Job.objects.filter(job_type="digest"))[-1]
    assert skipped.status == "done" and skipped.result["reason"] == "already_sent"


async def test_handler_rejects_unknown_period(monkeypatch):
    result = await handle_digest({"period": "hour"})
    assert result["status"] == "failed" and "hour" in result["error"]


async def test_failed_delivery_retries_without_duplicating_run_row(monkeypatch):
    """A failed channel delivery retries the job and reuses the digest_runs row.

    Regression: re-attempting the same (schedule, period) must not violate the
    unique constraint on digest_runs, and the retry must actually deliver.
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

    schedule = await Schedule.objects.create(
        name=SCHEDULE_NAME,
        cron_expr="0 9 * * *",
        timezone="UTC",
        job_type="digest",
        payload={"period": "day"},
        is_active=True,
    )

    await dispatcher.jobs.enqueue(job_type="digest", payload={"period": "day"}, schedule_id=schedule.id)
    assert await dispatcher.drain() == 1
    assert len(attempts) == 1

    failed = (await Job.objects.filter(job_type="digest"))[0]
    assert failed.status == "pending" and failed.error and "temporary" in failed.error
    assert failed.attempts == 1

    run = await DigestRun.objects.get(schedule_id=schedule.id)
    assert run.status == "failed"

    # Immediate retry: force the backoff to be over, then drain again
    await Job.objects.update_by_id(failed.id, run_at=datetime.now(timezone.utc))
    assert await dispatcher.drain() == 1

    assert len(attempts) == 2  # second attempt really went out
    retried = await Job.objects.get(id=failed.id)
    assert retried.status == "done"

    runs = await DigestRun.objects.filter(schedule_id=schedule.id)
    assert len(runs) == 1 and runs[0].status == "sent"
