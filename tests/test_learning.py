"""Tests for the learning loop: memory provenance, feedback commands, prompt edits."""

import uuid
from types import SimpleNamespace

import pytest

from app.agent import learning
from app.agent.learning import extract_json, run_learn
from app.agent.prompts import render_style_block
from app.models import AgentFeedback, AgentMessage, AgentSession, Tenant
from app.models.managers.agent_feedback_manager import agent_feedback
from app.models.managers.agent_memory_manager import agent_memory


def _uniq() -> str:
    return uuid.uuid4().hex[:10]


# --- pure helpers -----------------------------------------------------------


def test_extract_json_plain_and_fenced():
    assert extract_json('{"facts": []}')["facts"] == []
    assert extract_json('привет\n```json\n{"ops": [{"op": "delete", "id": 1}]}\n```')["ops"][0]["id"] == 1
    assert extract_json('text до [{"a": 1}] text после') == [{"a": 1}]
    assert extract_json("no json here") is None
    assert extract_json("") is None


def test_render_style_block():
    assert render_style_block(None) == ""
    assert render_style_block({}) == ""
    block = render_style_block({"tone": "сухо", "quiet_hours": "22:00–08:00", "junk": "x"})
    assert "Тон: сухо" in block
    assert "Тихие часы: 22:00–08:00" in block
    assert "junk" not in block


def test_clamp_confidence():
    assert learning._clamp_confidence(5) == 1.0
    assert learning._clamp_confidence(-3) == 0.1
    assert learning._clamp_confidence("bogus") == 0.5
    assert learning._clamp_confidence(0.7) == 0.7


# --- learn job (watermark-gated, no LLM call below threshold) ----------------


async def test_learn_skips_below_min_messages():
    max_id = await AgentMessage.objects.filter().order_by(AgentMessage.id.desc()).first()
    await learning.set_watermark(max_id.id if max_id else 0)

    session = await AgentSession.objects.create(channel="telegram", chat_id=f"t{_uniq()}", kind="private")
    for _ in range(2):
        await AgentMessage.objects.create(session_id=session.id, role="user", content="короткий вопрос")

    result = await run_learn(min_messages=5)
    assert result["status"] == "skipped"
    assert result["new_user_messages"] == 2

    await AgentMessage.objects.filter(session_id=session.id).delete()
    await AgentSession.objects.delete_by_id(session.id)


# --- memory provenance -------------------------------------------------------


async def test_snapshot_and_clear_facts_keep_meta():
    key = f"m9_pref_{_uniq()}"
    await agent_memory.write(key, "отвечать списками", source="learn", confidence=0.9)
    await learning.set_watermark(12345)

    snapshot = await agent_memory.snapshot(limit=50)
    assert all(f.scope != "meta" for f in snapshot)
    mine = [f for f in snapshot if f.key == key]
    assert mine and mine[0].source == "learn" and mine[0].confidence == 0.9

    removed = await agent_memory.clear_facts()
    assert removed >= 1
    assert await agent_memory.read(key) is None
    assert await learning.get_watermark() == 12345  # meta survives

    await agent_memory.write(learning._WM_KEY, None, scope=learning._META_SCOPE)


# --- feedback commands -------------------------------------------------------


async def test_bad_command_records_feedback_on_last_reply():
    from app.agent.runtime import _handle_feedback

    session = await AgentSession.objects.create(channel="telegram", chat_id=f"t{_uniq()}", kind="private")
    await AgentMessage.objects.create(session_id=session.id, role="assistant", content="первый ответ")
    rated = await AgentMessage.objects.create(session_id=session.id, role="assistant", content="хвостатый ответ")

    inbound = SimpleNamespace(channel="telegram", user_id="42")
    reply = await _handle_feedback(session, inbound, "/bad", "/bad слишком длинно")

    assert "слишком длинно" in reply
    notes = await agent_feedback.recent_notes("bad", limit=50)
    hit = [n for n in notes if n.session_id == session.id]
    assert len(hit) == 1 and hit[0].message_id == rated.id and hit[0].voter_external_id == "42"

    await AgentFeedback.objects.filter(session_id=session.id).delete()
    await AgentMessage.objects.filter(session_id=session.id).delete()
    await AgentSession.objects.delete_by_id(session.id)


async def test_good_command_without_assistant_message_is_noop():
    from app.agent.runtime import _handle_feedback

    session = await AgentSession.objects.create(channel="telegram", chat_id=f"t{_uniq()}", kind="private")
    inbound = SimpleNamespace(channel="telegram", user_id="1")
    reply = await _handle_feedback(session, inbound, "/good", "/good")
    assert "нечего" in reply or "Оценивать" in reply
    assert await AgentFeedback.objects.filter(session_id=session.id).first() is None
    await AgentSession.objects.delete_by_id(session.id)


# --- system prompt assembly ---------------------------------------------------


@pytest.fixture
async def workspace():
    # `pro`, not a tier of its own: learning is included from Pro up, and the
    # tier is a real CHECK-constrained value rather than a free-text label.
    tenant = await Tenant.objects.create(name=f"Learn WS {_uniq()}", slug=f"learn-{_uniq()}", plan="pro")
    yield tenant
    await Tenant.objects.delete_by_id(tenant.id)


async def test_build_system_prompt_includes_style_and_memory(workspace):
    from app.agent.runtime import build_system_prompt
    from app.core.tenant_context import tenant_scope
    from app.models.managers.tenant_manager import tenants

    key = f"m9_style_{_uniq()}"
    await Tenant.objects.update_by_id(workspace.id, agent_style={"tone": "без эмодзи", "language": "ru"})
    with tenant_scope(workspace.id):
        await agent_memory.write(key, "дайджест — только негатив", source="learn", confidence=0.8)
        prompt = await build_system_prompt()

    assert "Тон: без эмодзи" in prompt
    assert key in prompt

    await agent_memory.write(key, None)
    await Tenant.objects.update_by_id(workspace.id, agent_style=None)
    _ = tenants  # keep import meaningful for future refactors
