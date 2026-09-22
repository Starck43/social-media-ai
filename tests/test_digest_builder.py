"""Integration tests for digest builder against real DB (mocked channels/LLM)."""

import pytest

from app.models import DigestRun, Schedule
from app.services.digest import builder


@pytest.fixture(autouse=True)
async def _cleanup():
    await DigestRun.objects.delete()
    await Schedule.objects.delete(name__in=["it-digest", "it-digest-2"])
    yield
    await DigestRun.objects.delete()
    await Schedule.objects.delete(name__in=["it-digest", "it-digest-2"])


async def test_skipped_when_no_channels(monkeypatch):
    async def fake_broadcast(text):
        return {}

    monkeypatch.setattr("app.channels.registry.broadcast_digest", fake_broadcast)
    result = await builder.build_and_publish(period="day")
    assert result["status"] == "skipped"
    run = (await DigestRun.objects.filter())[-1]
    assert run.status == "skipped"


async def test_sent_and_idempotent(monkeypatch):
    sent_calls = []

    async def fake_broadcast(text):
        sent_calls.append(text)
        return {"telegram": {"success": True, "message_id": 42}}

    async def fake_summarize(data):
        return "Спокойный день", {"model": "test-model"}

    monkeypatch.setattr("app.channels.registry.broadcast_digest", fake_broadcast)
    monkeypatch.setattr(builder, "_summarize", fake_summarize)

    schedule = await Schedule.objects.create(
        name="it-digest",
        cron_expr="0 9 * * *",
        timezone="UTC",
        job_type="digest",
        payload={"period": "day"},
        is_active=True,
    )

    result = await builder.build_and_publish(period="day", schedule_id=schedule.id)
    assert result["status"] == "sent"
    assert len(sent_calls) == 1
    run = (await DigestRun.objects.filter())[-1]
    assert run.status == "sent" and run.message_id == "42"

    # Idempotency: same schedule+period is not re-sent
    again = await builder.build_and_publish(period="day", schedule_id=schedule.id)
    assert again["status"] == "skipped"
    assert again["reason"] == "already_sent"
    assert len(sent_calls) == 1

    # Manual run (schedule_id=None) is never blocked
    manual = await builder.build_and_publish(period="day")
    assert manual["status"] == "sent"
    assert len(sent_calls) == 2


async def test_failed_delivery_recorded_and_retryable(monkeypatch):
    async def fake_broadcast(text):
        return {"telegram": {"success": False, "error": "boom"}}

    async def fake_summarize(data):
        return None, {"model": None}

    monkeypatch.setattr("app.channels.registry.broadcast_digest", fake_broadcast)
    monkeypatch.setattr(builder, "_summarize", fake_summarize)

    # Delivery failure is retryable: it raises so the job queue re-runs it.
    with pytest.raises(builder.DigestDeliveryError) as exc:
        await builder.build_and_publish(period="day")
    assert "boom" in str(exc.value)

    run = (await DigestRun.objects.filter())[-1]
    assert run.status == "failed" and "boom" in (run.error or "")


async def test_scheduled_retry_reuses_run_row(monkeypatch):
    """A second attempt for the same schedule+period must not violate the unique index."""
    attempts = []

    async def fake_broadcast(text):
        attempts.append(text)
        if len(attempts) == 1:
            return {"telegram": {"success": False, "error": "503"}}
        return {"telegram": {"success": True, "message_id": 7}}

    async def fake_summarize(data):
        return "ok", {"model": "test-model"}

    monkeypatch.setattr("app.channels.registry.broadcast_digest", fake_broadcast)
    monkeypatch.setattr(builder, "_summarize", fake_summarize)

    schedule = await Schedule.objects.create(
        name="it-digest-2",
        cron_expr="0 9 * * *",
        timezone="UTC",
        job_type="digest",
        payload={"period": "day"},
        is_active=True,
    )

    with pytest.raises(builder.DigestDeliveryError):
        await builder.build_and_publish(period="day", schedule_id=schedule.id)

    result = await builder.build_and_publish(period="day", schedule_id=schedule.id)
    assert result["status"] == "sent"

    rows = await DigestRun.objects.filter(schedule_id=schedule.id)
    assert len(rows) == 1  # one row per (schedule, period), updated in place
    assert rows[0].status == "sent" and rows[0].message_id == "7"

    # And after success the period is locked in
    assert (await builder.build_and_publish(period="day", schedule_id=schedule.id))["reason"] == "already_sent"


async def test_aggregate_shape():
    data, start, end = await builder.aggregate("week")
    assert data["period"] == "week"
    assert (end - start).days == 6
    assert "sentiment" in data and "topics" in data
