"""Learning/reflect LLM spend feeds the daily cap via jobs.llm_cost."""

import uuid
from datetime import datetime, timezone

from app.agent import learning
from app.agent.learning import run_learn
from app.jobs import dispatcher as dispatcher_module
from app.models import AgentMessage, AgentSession, Job


def _uniq() -> str:
    return uuid.uuid4().hex[:10]


async def _session_with_users(n: int) -> AgentSession:
    session = await AgentSession.objects.create(channel="telegram", chat_id=f"lc{_uniq()}", kind="private")
    for i in range(n):
        await AgentMessage.objects.create(session_id=session.id, role="user", content=f"vopros {i} {_uniq()}")
    return session


async def _make_job(**kwargs) -> Job:
    kwargs.setdefault("job_type", "learn")
    kwargs.setdefault("payload", {})
    kwargs.setdefault("run_at", datetime.now(timezone.utc))
    return await Job.objects.create(**kwargs)


async def test_run_learn_returns_priced_llm_cost(monkeypatch):
    max_id = await AgentMessage.objects.filter().order_by(AgentMessage.id.desc()).first()
    await learning.set_watermark(max_id.id if max_id else 0)
    session = await _session_with_users(8)

    async def fake_chat(messages, **kwargs):
        return {"content": '{"facts": []}', "usage": {"cost": 0.007}}

    monkeypatch.setattr("app.services.ai.llm_client.chat_with_fallback", fake_chat)

    result = await run_learn(min_messages=8)
    assert result["status"] == "ok"
    assert result["llm_cost"] == 0.007

    await AgentMessage.objects.filter(session_id=session.id).delete()
    await AgentSession.objects.delete_by_id(session.id)


async def test_run_learn_skipped_carries_zero_cost():
    max_id = await AgentMessage.objects.filter().order_by(AgentMessage.id.desc()).first()
    await learning.set_watermark(max_id.id if max_id else 0)
    session = await _session_with_users(2)

    result = await run_learn(min_messages=8)
    assert result["status"] == "skipped"
    assert result["llm_cost"] == 0.0

    await AgentMessage.objects.filter(session_id=session.id).delete()
    await AgentSession.objects.delete_by_id(session.id)


async def test_run_reflect_returns_priced_llm_cost(monkeypatch):
    await learning.set_watermark(0)
    session = await AgentSession.objects.create(channel="telegram", chat_id=f"rc{_uniq()}", kind="private")
    rated = await AgentMessage.objects.create(session_id=session.id, role="assistant", content="answer")
    from app.models.managers.agent_feedback_manager import agent_feedback

    await agent_feedback.create(
        session_id=session.id, message_id=rated.id, vote="bad", note="long", voter_external_id="1"
    )

    async def fake_chat(messages, **kwargs):
        return {"content": '{"ops": [], "prompt_advice": ""}', "usage": {"cost": 0.003}}

    monkeypatch.setattr("app.services.ai.llm_client.chat_with_fallback", fake_chat)

    result = await learning.run_reflect()
    assert result["status"] == "ok"
    assert result["llm_cost"] == 0.003

    from app.models import AgentFeedback

    await AgentFeedback.objects.filter(session_id=session.id).delete()
    await AgentMessage.objects.filter(session_id=session.id).delete()
    await AgentSession.objects.delete_by_id(session.id)


async def test_run_learn_skipped_at_daily_cap(monkeypatch):
    from app.services.tenancy import resolver as resolver_module

    async def full_limit():
        return 5.0

    async def spent():
        return 99.0

    async def boom(messages, **kwargs):
        raise AssertionError("model must not be called at cap")

    monkeypatch.setattr(resolver_module, "current_daily_cost_limit", full_limit)
    monkeypatch.setattr(resolver_module, "daily_cost_today", spent)
    monkeypatch.setattr("app.services.ai.llm_client.chat_with_fallback", boom)

    await learning.set_watermark(0)
    session = await _session_with_users(8)
    result = await run_learn(min_messages=1)
    assert result["status"] == "skipped"
    assert result["reason"] == "cost_cap"

    await AgentMessage.objects.filter(session_id=session.id).delete()
    await AgentSession.objects.delete_by_id(session.id)


async def test_run_reflect_skipped_at_daily_cap(monkeypatch):
    from app.services.tenancy import resolver as resolver_module

    async def full_limit():
        return 5.0

    async def spent():
        return 99.0

    async def boom(messages, **kwargs):
        raise AssertionError("model must not be called at cap")

    monkeypatch.setattr(resolver_module, "current_daily_cost_limit", full_limit)
    monkeypatch.setattr(resolver_module, "daily_cost_today", spent)
    monkeypatch.setattr("app.services.ai.llm_client.chat_with_fallback", boom)

    key = f"capfact_{_uniq()}"
    from app.models.managers.agent_memory_manager import agent_memory

    await agent_memory.write(key, "fact", source="learn", confidence=0.9)
    result = await learning.run_reflect()
    assert result["status"] == "skipped"
    assert result["reason"] == "cost_cap"
    await agent_memory.write(key, None)


async def test_execute_job_persists_learn_llm_cost():
    async def fake_learn(payload):
        return {"status": "ok", "llm_cost": 0.011}

    import app.jobs.handlers as handlers

    orig = handlers.handle_learn
    handlers.HANDLERS["learn"] = lambda payload: fake_learn(payload)
    dispatcher_module.HANDLERS["learn"] = handlers.HANDLERS["learn"]
    try:
        job = await _make_job()
        await dispatcher_module.execute_job(job, dispatcher_module.HANDLERS["learn"])
        stored = await Job.objects.get(id=job.id)
        assert stored.status == "done"
        assert stored.llm_cost == 0.011
        await Job.objects.delete_by_id(job.id)
    finally:
        handlers.HANDLERS["learn"] = orig
        dispatcher_module.HANDLERS["learn"] = orig


async def test_daily_cost_today_sums_jobs_llm_cost():
    from app.models.managers.digest_run_manager import digest_runs
    from app.services.tenancy.resolver import daily_cost_today

    await Job.objects.filter().delete()
    await AgentMessage.objects.filter().delete()
    await digest_runs.filter().delete()

    await Job.objects.create(job_type="learn", payload={}, llm_cost=0.02, run_at=datetime.now(timezone.utc))
    total = await daily_cost_today()
    assert total == 0.02

    await Job.objects.filter().delete()
