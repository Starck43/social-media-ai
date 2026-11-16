"""Integration tests for digest builder against real DB (mocked channels/LLM)."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models import AgentTask, DigestRun
from app.models.managers.digest_run_manager import (
    DIGEST_RUN_IN_PROGRESS,
    FORCED_TAKEOVER_AFTER,
    PARTIAL_DELIVERY_REVIEW,
    DigestRunInProgress,
)
from app.services.digest import builder
from tests.shared.platform_catalog import shared_platform

TASK_NAMES = [
    "it-digest",
    "it-digest-2",
    "it-digest-cost",
    "it-digest-partial",
    "it-digest-chunks",
    "it-digest-busy",
    "it-digest-race",
    "it-digest-force-race",
    "it-digest-version",
]


@pytest.fixture(autouse=True)
async def _cleanup():
    from app.core.config import settings
    from app.core.tenant_context import tenant_scope
    from app.models import Tenant

    tenant = await Tenant.objects.get(slug=settings.DEFAULT_TENANT_SLUG)
    with tenant_scope(tenant.id):
        await DigestRun.objects.delete()
        await AgentTask.objects.delete(name__in=TASK_NAMES)
        yield
        await DigestRun.objects.delete()
        await AgentTask.objects.delete(name__in=TASK_NAMES)


async def test_skipped_when_no_channels(monkeypatch):
    async def fake_broadcast(text):
        return {}

    async def fake_summarize(data):
        return "Спокойный день", {"model": "test-model"}

    monkeypatch.setattr("app.channels.registry.broadcast_digest", fake_broadcast)
    monkeypatch.setattr(builder, "_summarize", fake_summarize)
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

    schedule = await AgentTask.objects.create(
        name="it-digest",
        cron_expr="0 9 * * *",
        job_type="digest",
        payload={"period": "day"},
        is_active=True,
    )

    result = await builder.build_and_publish(period="day", agent_task_id=schedule.id)
    assert result["status"] == "sent"
    assert len(sent_calls) == 1
    run = (await DigestRun.objects.filter())[-1]
    assert run.status == "sent" and run.message_id == "42"

    # Idempotency: same schedule+period is not re-sent
    again = await builder.build_and_publish(period="day", agent_task_id=schedule.id)
    assert again["status"] == "skipped"
    assert again["reason"] == "already_sent"
    assert len(sent_calls) == 1

    # Manual run (agent_task_id=None) is never blocked
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

    schedule = await AgentTask.objects.create(
        name="it-digest-2",
        cron_expr="0 9 * * *",
        job_type="digest",
        payload={"period": "day"},
        is_active=True,
    )

    with pytest.raises(builder.DigestDeliveryError):
        await builder.build_and_publish(period="day", agent_task_id=schedule.id)

    result = await builder.build_and_publish(period="day", agent_task_id=schedule.id)
    assert result["status"] == "sent"

    rows = await DigestRun.objects.filter(agent_task_id=schedule.id)
    assert len(rows) == 1  # one row per (schedule, period), updated in place
    assert rows[0].status == "sent" and rows[0].message_id == "7"

    # And after success the period is locked in
    assert (await builder.build_and_publish(period="day", agent_task_id=schedule.id))["reason"] == "already_sent"


async def _schedule(name):
    return await AgentTask.objects.create(
        name=name, cron_expr="0 9 * * *", job_type="digest", payload={"period": "day"}, is_active=True
    )


async def test_partial_delivery_is_terminal_and_not_resent(monkeypatch):
    """One recipient succeeded, another failed: no retry error and no automatic resend."""
    broadcasts, summaries = [], []

    async def fake_broadcast(text):
        broadcasts.append(text)
        return {
            "telegram:111": {"success": True, "message_id": 5, "message_ids": [5]},
            "max:222": {"success": False, "error": "HTTP 503", "message_ids": []},
        }

    async def fake_summarize(data):
        summaries.append(data)
        return "ok", {"model": "test-model", "cost": 0.1}

    monkeypatch.setattr("app.channels.registry.broadcast_digest", fake_broadcast)
    monkeypatch.setattr(builder, "_summarize", fake_summarize)
    schedule = await _schedule("it-digest-partial")

    result = await builder.build_and_publish(period="day", agent_task_id=schedule.id)
    assert result["status"] == "failed" and result["error"] == PARTIAL_DELIVERY_REVIEW
    rows = await DigestRun.objects.filter(agent_task_id=schedule.id)
    assert len(rows) == 1 and rows[0].status == "failed"
    assert rows[0].error.startswith(PARTIAL_DELIVERY_REVIEW) and "max:222" in rows[0].error
    assert rows[0].message_id == "5" and rows[0].llm_cost == pytest.approx(0.1)

    # A later attempt for the same schedule+period neither rebuilds nor resends.
    again = await builder.build_and_publish(period="day", agent_task_id=schedule.id)
    assert again == {"status": "failed", "error": PARTIAL_DELIVERY_REVIEW}
    assert len(broadcasts) == 1 and len(summaries) == 1
    rows = await DigestRun.objects.filter(agent_task_id=schedule.id)
    assert rows[0].error.startswith(PARTIAL_DELIVERY_REVIEW) and rows[0].llm_cost == pytest.approx(0.1)

    # An explicit force run is the operator's resend decision.
    forced = await builder.build_and_publish(period="day", agent_task_id=schedule.id, force=True)
    assert forced["status"] == "failed" and len(broadcasts) == 2


async def test_failed_recipient_with_delivered_chunks_is_partial(monkeypatch):
    """A split message that failed after its first chunk must not be resent from the start."""

    async def fake_broadcast(text):
        return {"telegram:111": {"success": False, "error": "HTTP 502", "message_ids": [11], "message_id": 11}}

    async def fake_summarize(data):
        return None, {"model": None}

    monkeypatch.setattr("app.channels.registry.broadcast_digest", fake_broadcast)
    monkeypatch.setattr(builder, "_summarize", fake_summarize)
    schedule = await _schedule("it-digest-chunks")

    result = await builder.build_and_publish(period="day", agent_task_id=schedule.id)
    assert result["status"] == "failed" and result["error"] == PARTIAL_DELIVERY_REVIEW
    assert (await DigestRun.objects.filter(agent_task_id=schedule.id))[0].error.startswith(PARTIAL_DELIVERY_REVIEW)


async def test_manual_partial_delivery_does_not_block_next_manual_run(monkeypatch):
    calls = []

    async def fake_broadcast(text):
        calls.append(text)
        return {
            "telegram:111": {"success": True, "message_id": 1},
            "telegram:333": {"success": False, "error": "HTTP 403"},
        }

    async def fake_summarize(data):
        return None, {"model": None}

    monkeypatch.setattr("app.channels.registry.broadcast_digest", fake_broadcast)
    monkeypatch.setattr(builder, "_summarize", fake_summarize)

    assert (await builder.build_and_publish(period="day"))["error"] == PARTIAL_DELIVERY_REVIEW
    assert (await builder.build_and_publish(period="day"))["error"] == PARTIAL_DELIVERY_REVIEW
    assert len(calls) == 2


async def test_scheduled_attempt_stops_when_run_row_is_in_flight(monkeypatch):
    """A `pending` run row belongs to another attempt: no summary cost, no send, row untouched."""
    calls = []

    async def fake_broadcast(text):
        calls.append("broadcast")
        return {"telegram": {"success": True, "message_id": 9}}

    async def fake_summarize(data):
        calls.append("summary")
        return "ok", {"model": "test-model", "cost": 0.2}

    monkeypatch.setattr("app.channels.registry.broadcast_digest", fake_broadcast)
    monkeypatch.setattr(builder, "_summarize", fake_summarize)
    schedule = await _schedule("it-digest-busy")
    start, end = builder.period_bounds("day")
    in_flight = await DigestRun.objects.create(
        agent_task_id=schedule.id, period="day", period_start=start, period_end=end, channel="auto", status="pending"
    )

    result = await builder.build_and_publish(period="day", agent_task_id=schedule.id)
    assert result == {"status": "failed", "error": DIGEST_RUN_IN_PROGRESS}
    assert calls == []
    row = await DigestRun.objects.get(id=in_flight.id)
    assert row.status == "pending" and row.error is None and row.llm_cost is None

    # A forced resend does not race a row that may still be in flight.
    early = await builder.build_and_publish(period="day", agent_task_id=schedule.id, force=True)
    assert early == {"status": "failed", "error": DIGEST_RUN_IN_PROGRESS} and calls == []

    # Once the row is stale, the operator's explicit force run takes it over and delivers.
    stale_at = datetime.now(timezone.utc) - FORCED_TAKEOVER_AFTER - timedelta(minutes=1)
    assert await DigestRun.objects.filter(id=in_flight.id).update(updated_at=stale_at) == 1
    forced = await builder.build_and_publish(period="day", agent_task_id=schedule.id, force=True)
    assert forced["status"] == "sent" and calls == ["summary", "broadcast"]
    rows = await DigestRun.objects.filter(agent_task_id=schedule.id)
    assert len(rows) == 1 and rows[0].id == in_flight.id and rows[0].status == "sent"


async def test_concurrent_scheduled_attempts_deliver_once(monkeypatch):
    """Two attempts for the same schedule+period at once: one builds and sends, the other stops."""
    broadcasts, summaries = [], []
    first_summary_started = asyncio.Event()
    release_summary = asyncio.Event()

    async def fake_broadcast(text):
        broadcasts.append(text)
        return {"telegram": {"success": True, "message_id": 11}}

    async def fake_summarize(data):
        summaries.append(data)
        first_summary_started.set()
        await release_summary.wait()
        return "ok", {"model": "test-model"}

    monkeypatch.setattr("app.channels.registry.broadcast_digest", fake_broadcast)
    monkeypatch.setattr(builder, "_summarize", fake_summarize)
    schedule = await _schedule("it-digest-race")

    first = asyncio.create_task(builder.build_and_publish(period="day", agent_task_id=schedule.id))
    await asyncio.wait_for(first_summary_started.wait(), timeout=10)
    second = await builder.build_and_publish(period="day", agent_task_id=schedule.id)
    release_summary.set()
    winner = await asyncio.wait_for(first, timeout=10)

    assert second == {"status": "failed", "error": DIGEST_RUN_IN_PROGRESS}
    assert winner["status"] == "sent"
    assert len(summaries) == 1 and len(broadcasts) == 1
    rows = await DigestRun.objects.filter(agent_task_id=schedule.id)
    assert len(rows) == 1 and rows[0].status == "sent" and rows[0].message_id == "11"


async def test_concurrent_forced_resends_deliver_once(monkeypatch):
    """A second forced resend while the first is still building stops before cost or send."""
    broadcasts, summaries = [], []
    first_summary_started = asyncio.Event()
    release_summary = asyncio.Event()

    async def fake_broadcast(text):
        broadcasts.append(text)
        return {"telegram": {"success": True, "message_id": 12}}

    async def fake_summarize(data):
        summaries.append(data)
        if len(summaries) == 1:  # only the first build waits; a second build must fail the test, not hang it
            first_summary_started.set()
            await release_summary.wait()
        return "ok", {"model": "test-model"}

    monkeypatch.setattr("app.channels.registry.broadcast_digest", fake_broadcast)
    monkeypatch.setattr(builder, "_summarize", fake_summarize)
    schedule = await _schedule("it-digest-force-race")

    first = asyncio.create_task(builder.build_and_publish(period="day", agent_task_id=schedule.id, force=True))
    await asyncio.wait_for(first_summary_started.wait(), timeout=10)
    second = await builder.build_and_publish(period="day", agent_task_id=schedule.id, force=True)
    release_summary.set()
    winner = await asyncio.wait_for(first, timeout=10)

    assert second == {"status": "failed", "error": DIGEST_RUN_IN_PROGRESS}
    assert winner["status"] == "sent"
    assert len(summaries) == 1 and len(broadcasts) == 1
    rows = await DigestRun.objects.filter(agent_task_id=schedule.id)
    assert len(rows) == 1 and rows[0].status == "sent" and rows[0].message_id == "12"


async def test_simultaneous_claims_of_one_stale_row_have_one_winner():
    """The claim bumps `updated_at`, so a claim built on the previous version matches nothing."""
    from app.models.managers.digest_run_manager import digest_runs

    schedule = await _schedule("it-digest-version")
    start, end = builder.period_bounds("day")
    stale_at = datetime.now(timezone.utc) - FORCED_TAKEOVER_AFTER - timedelta(minutes=1)
    row = await DigestRun.objects.create(
        agent_task_id=schedule.id, period="day", period_start=start, period_end=end, channel="auto", status="pending"
    )
    assert await DigestRun.objects.filter(id=row.id).update(updated_at=stale_at) == 1
    observed = await DigestRun.objects.get(id=row.id)

    outcomes = await asyncio.gather(
        *(
            digest_runs.start_run(
                agent_task_id=schedule.id, period="day", period_start=start, period_end=end, force=True
            )
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    winners = [o for o in outcomes if not isinstance(o, BaseException)]
    losers = [o for o in outcomes if isinstance(o, DigestRunInProgress)]
    assert len(winners) == 1 and len(losers) == 1 and winners[0].id == row.id
    claimed = await DigestRun.objects.get(id=row.id)
    assert claimed.status == "pending" and claimed.updated_at > observed.updated_at
    # A claim still holding the pre-claim version cannot take the row.
    stale_claim = DigestRun.objects.filter(id=row.id, status="pending", updated_at=observed.updated_at)
    assert await stale_claim.update(status="pending") == 0


def test_delivered_any_classification():
    assert builder._delivered_any({"success": True})
    assert builder._delivered_any({"success": False, "message_ids": [3]})
    assert not builder._delivered_any({"success": False, "error": "boom"})
    assert not builder._delivered_any({"success": False, "message_ids": []})


async def test_aggregate_shape():
    data, start, end = await builder.aggregate("week")
    assert data["period"] == "week"
    assert (end - start).days == 6
    assert "sentiment" in data and "topics" in data


async def test_summarize_skipped_at_daily_cap(monkeypatch):
    """At the cap _summarize never touches the model (digest still renders)."""
    from app.services.tenancy import resolver as resolver_module

    async def full_limit():
        return 5.0

    async def spent():
        return 99.0

    async def boom(*args, **kwargs):  # model resolution must never run
        raise AssertionError("model must not be resolved at cap")

    monkeypatch.setattr(resolver_module, "current_daily_cost_limit", full_limit)
    monkeypatch.setattr(resolver_module, "daily_cost_today", spent)
    from app.models.managers.llm_model_manager import LLMModelManager

    monkeypatch.setattr(LLMModelManager, "resolve_default_model", boom)

    summary, info = await builder._summarize({"stats": {}})
    assert summary is None
    assert info["cost_cap"] is True


async def test_run_records_llm_cost(monkeypatch):
    """The summary's priced cost lands on DigestRun.llm_cost (the cap metric)."""

    async def fake_broadcast(text):
        return {"telegram": {"success": True, "message_id": 1}}

    async def fake_summarize(data):
        return "ok", {"model": "test-model", "cost": 0.42}

    monkeypatch.setattr("app.channels.registry.broadcast_digest", fake_broadcast)
    monkeypatch.setattr(builder, "_summarize", fake_summarize)

    result = await builder.build_and_publish(period="day")
    assert result["status"] == "sent"
    run = (await DigestRun.objects.filter())[-1]
    assert run.llm_cost == 0.42


async def test_retry_accumulates_llm_cost(monkeypatch):
    """Every summary attempt was really paid for — retries add up, not overwrite."""
    attempts = []

    async def fake_broadcast(text):
        attempts.append(text)
        if len(attempts) == 1:
            return {"telegram": {"success": False, "error": "503"}}
        return {"telegram": {"success": True, "message_id": 9}}

    async def fake_summarize(data):
        return "ok", {"model": "test-model", "cost": 0.1}

    monkeypatch.setattr("app.channels.registry.broadcast_digest", fake_broadcast)
    monkeypatch.setattr(builder, "_summarize", fake_summarize)

    schedule = await AgentTask.objects.create(
        name="it-digest-cost",
        cron_expr="0 9 * * *",
        job_type="digest",
        payload={"period": "day"},
        is_active=True,
    )

    with pytest.raises(builder.DigestDeliveryError):
        await builder.build_and_publish(period="day", agent_task_id=schedule.id)
    result = await builder.build_and_publish(period="day", agent_task_id=schedule.id)
    assert result["status"] == "sent"

    rows = await DigestRun.objects.filter(agent_task_id=schedule.id)
    assert len(rows) == 1
    assert rows[0].llm_cost == pytest.approx(0.2)


async def test_handle_digest_wires_sources_and_scenario(monkeypatch):
    """The handler passes the task's sources and scenario analyze_type into the builder."""
    from app.jobs.handlers import handle_digest
    from app.models import AgentScenario, AgentTask, Platform, Source
    from app.types import SourceType
    from app.web.tasks import _replace_task_sources

    platform = await shared_platform("vk")
    source = await Source.objects.create(
        platform_id=platform.id,
        name="digest-source",
        source_type=SourceType.CHANNEL,
        external_id="digest-source",
        is_active=True,
    )
    scenario = await AgentScenario.objects.create(
        name="digest-scn",
        analysis_types=["toxicity", "brand_mentions"],
    )
    task = await AgentTask.objects.create(
        name=f"t_{uuid.uuid4().hex[:6]}",
        job_type="digest",
        cron_expr="@once",
        payload={"period": "week", "group_by": "sources"},
        agent_scenario_id=scenario.id,
        is_active=True,
    )
    try:
        await _replace_task_sources(task.id, [source.id], task.tenant_id)

        captured = {}

        async def fake_build(period, agent_task_id, force, source_ids, group_by, time_breakdown, scenario_id):
            captured.update(
                period=period,
                agent_task_id=agent_task_id,
                force=force,
                source_ids=source_ids,
                group_by=group_by,
                time_breakdown=time_breakdown,
                scenario_id=scenario_id,
            )
            return {"status": "sent", "results": {}}

        monkeypatch.setattr("app.services.digest.builder.build_and_publish", fake_build)
        await handle_digest({"agent_task_id": task.id})

        assert captured["period"] == "week"
        assert captured["source_ids"] == [source.id]
        assert captured["group_by"] == "sources"
        assert captured["time_breakdown"] is False
        assert captured["scenario_id"] == scenario.id
    finally:
        await AgentTask.objects.delete_by_id(task.id)
        await AgentScenario.objects.delete_by_id(scenario.id)
        await Source.objects.delete_by_id(source.id)


async def test_broadcast_digest_sends_to_tenant_digest_targets(monkeypatch):
    """Step 3: every active tenant channel with is_digest_target=True gets the digest."""
    from app.channels import registry as registry_module
    from app.core.tenant_context import tenant_scope
    from app.models import Tenant, TenantChannel

    sent = []

    class StubChannel:
        async def send(self, chat_id, text, parse_mode=None):
            sent.append((chat_id, text))
            return {"success": True, "message_id": 1}

    monkeypatch.setattr(registry_module.settings, "TELEGRAM_DIGEST_CHANNEL_ID", "")
    monkeypatch.setattr(registry_module.settings, "MAX_CHANNEL_ID", "")
    monkeypatch.setattr(registry_module, "get_channel", lambda name: StubChannel())

    tenant = await Tenant.objects.get(slug="owner")
    rows = [
        await TenantChannel.objects.create(
            tenant_id=tenant.id, channel="telegram", chat_id="111", kind="channel", is_digest_target=True
        ),
        await TenantChannel.objects.create(
            tenant_id=tenant.id, channel="max", chat_id="222", kind="channel", is_digest_target=True
        ),
        await TenantChannel.objects.create(
            tenant_id=tenant.id, channel="telegram", chat_id="333", kind="private", is_digest_target=False
        ),
    ]
    try:
        with tenant_scope(tenant.id):
            results = await registry_module.broadcast_digest("hello")
        assert len(sent) == 2
        assert ("111", "hello") in sent and ("222", "hello") in sent
        assert len(results) == 2
    finally:
        for row in rows:
            await TenantChannel.objects.delete_by_id(row.id)
