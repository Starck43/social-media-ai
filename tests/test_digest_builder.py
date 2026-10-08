"""Integration tests for digest builder against real DB (mocked channels/LLM)."""

import uuid

import pytest

from app.models import AgentTask, DigestRun
from app.services.digest import builder


@pytest.fixture(autouse=True)
async def _cleanup():
    await DigestRun.objects.delete()
    await AgentTask.objects.delete(name__in=["it-digest", "it-digest-2", "it-digest-cost"])
    yield
    await DigestRun.objects.delete()
    await AgentTask.objects.delete(name__in=["it-digest", "it-digest-2", "it-digest-cost"])


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

    platform = await Platform.objects.create(
        name=f"vk_{uuid.uuid4().hex[:8]}",
        platform_type="vk",
        base_url="https://vk.com",
        params={},
    )
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
        await Platform.objects.delete_by_id(platform.id)


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
