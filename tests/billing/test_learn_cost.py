"""Learning/reflect LLM spend feeds the daily cap via jobs.llm_cost."""

import uuid
from datetime import datetime, timezone

import pytest

from app.agent import learning
from app.agent.identity import memory_actor_scope
from app.agent.learning import run_learn
from app.core.tenant_context import tenant_scope
from app.jobs import dispatcher as dispatcher_module
from app.models import AgentMessage, AgentSession, Job, Role, Tenant, User
from app.models.managers.tenant_manager import tenant_channels, tenant_users
from app.types import PlatformRoleType


def _uniq() -> str:
    return uuid.uuid4().hex[:10]


@pytest.fixture
async def actor_workspace():
    tenant = await Tenant.objects.create(name="Learning cost", slug=f"learn-cost-{_uniq()}", plan="business")
    role = await Role.objects.get_by_codename(PlatformRoleType.USER)
    user = await User.objects.create_user(
        username=f"learn-cost-{_uniq()}",
        email=f"learn-cost-{_uniq()}@example.com",
        password="test-only-learning-password",
        role_id=role.id,
    )
    try:
        yield tenant, user
    finally:
        await User.objects.delete_user(user.id)
        await Tenant.objects.delete_by_id(tenant.id)


async def _session_with_users(n: int, tenant: Tenant, user: User) -> AgentSession:
    chat_id = f"lc{_uniq()}"
    with tenant_scope(tenant.id):
        membership = await tenant_users.add_member(
            tenant_id=tenant.id, channel="telegram", external_user_id=chat_id, role="owner"
        )
        await tenant_users.update_by_id(membership.id, user_id=user.id)
        await tenant_channels.bind(tenant_id=tenant.id, channel="telegram", chat_id=chat_id)
        session = await AgentSession.objects.create(channel="telegram", chat_id=chat_id, kind="private")
        for i in range(n):
            await AgentMessage.objects.create(session_id=session.id, role="user", content=f"vopros {i} {_uniq()}")
    return session


async def _make_job(**kwargs) -> Job:
    kwargs.setdefault("job_type", "learn")
    kwargs.setdefault("payload", {})
    kwargs.setdefault("run_at", datetime.now(timezone.utc))
    return await Job.objects.create(**kwargs)


async def test_run_learn_returns_priced_llm_cost(monkeypatch, actor_workspace):
    tenant, user = actor_workspace
    await _session_with_users(8, tenant, user)

    async def fake_chat(messages, **kwargs):
        return {"content": '{"facts": []}', "usage": {"cost": 0.007}}

    monkeypatch.setattr("app.services.ai.llm_client.chat_with_fallback", fake_chat)

    with tenant_scope(tenant.id), memory_actor_scope(user.id, True):
        await learning.set_watermark(0)
        result = await run_learn(min_messages=8)
    assert result["status"] == "ok"
    assert result["llm_cost"] == 0.007


async def test_run_learn_skipped_carries_zero_cost(actor_workspace):
    tenant, user = actor_workspace
    await _session_with_users(2, tenant, user)

    with tenant_scope(tenant.id), memory_actor_scope(user.id, True):
        await learning.set_watermark(0)
        result = await run_learn(min_messages=8)
    assert result["status"] == "skipped"
    assert result["llm_cost"] == 0.0


async def test_run_reflect_returns_priced_llm_cost(monkeypatch, actor_workspace):
    tenant, user = actor_workspace
    session = await _session_with_users(0, tenant, user)
    with tenant_scope(tenant.id):
        rated = await AgentMessage.objects.create(session_id=session.id, role="assistant", content="answer")
    from app.models.managers.agent_feedback_manager import agent_feedback

    with tenant_scope(tenant.id):
        await agent_feedback.create(
            session_id=session.id, message_id=rated.id, vote="bad", note="long", voter_external_id="1"
        )

    async def fake_chat(messages, **kwargs):
        return {"content": '{"ops": [], "prompt_advice": ""}', "usage": {"cost": 0.003}}

    monkeypatch.setattr("app.services.ai.llm_client.chat_with_fallback", fake_chat)

    with tenant_scope(tenant.id), memory_actor_scope(user.id, True):
        await learning.set_watermark(0)
        result = await learning.run_reflect()
    assert result["status"] == "ok"
    assert result["llm_cost"] == 0.003


async def test_run_learn_skipped_at_daily_cap(monkeypatch, actor_workspace):
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

    tenant, user = actor_workspace
    await _session_with_users(8, tenant, user)
    with tenant_scope(tenant.id), memory_actor_scope(user.id, True):
        await learning.set_watermark(0)
        result = await run_learn(min_messages=1)
    assert result["status"] == "skipped"
    assert result["reason"] == "cost_cap"


async def test_run_reflect_skipped_at_daily_cap(monkeypatch, actor_workspace):
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

    tenant, user = actor_workspace
    await _session_with_users(0, tenant, user)
    key = f"capfact_{_uniq()}"
    from app.models.managers.agent_memory_manager import agent_memory

    # Reflection needs one actor fact to reach the cap gate. The fact is written
    # explicitly for this workspace: the suite's operator bypass stamps the
    # bootstrap tenant otherwise.
    with tenant_scope(tenant.id), memory_actor_scope(user.id, True):
        await agent_memory.create(
            user_id=user.id, tenant_id=tenant.id, scope="global", key=key, value="fact", source="learn", confidence=0.9
        )
    with tenant_scope(tenant.id), memory_actor_scope(user.id, True):
        result = await learning.run_reflect()
    assert result["status"] == "skipped"
    assert result["reason"] == "cost_cap"


async def test_execute_job_persists_learn_llm_cost(actor_workspace):
    async def fake_learn(payload):
        return {"status": "ok", "llm_cost": 0.011}

    import app.jobs.handlers as handlers

    tenant, user = actor_workspace
    with tenant_scope(tenant.id):
        await tenant_users.add_web_member(tenant_id=tenant.id, user_id=user.id, role="owner")

    orig = handlers.handle_learn
    handlers.HANDLERS["learn"] = lambda payload: fake_learn(payload)
    dispatcher_module.HANDLERS["learn"] = handlers.HANDLERS["learn"]
    try:
        # The dispatcher resolves the job's personal catalog owner from its initiator.
        pending = await _make_job(tenant_id=tenant.id, initiated_by_user_id=user.id)
        job = await Job.objects.claim_job(pending.id)
        assert job is not None and job.status == "running"
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
