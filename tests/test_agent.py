"""Agent chat-loop tests: runtime confirmation flow + channel listener.

Covers what the runtime depends on and nothing executes against real LLM or
HTTP: `agent.runtime._chat` is stubbed, so tests exercise only our loop —
confirmation gating, tool dispatch, cost-cap short-circuit, owner allowlist —
not the model. Listener tests stub `handle_inbound` the same way.

Uses the real database (like test_digest_e2e.py) with fixture-level cleanup of
sessions created by the tests.
"""

import asyncio

import pytest

from app.agent import runtime as agent_runtime
from app.channels.base import Inbound
from app.channels import listener as listener_module
from app.models import AgentSession


@pytest.fixture
async def _clean_sessions():
    await AgentSession.objects.delete()
    yield
    await AgentSession.objects.delete()


def _inbound(text: str = "hi", **overrides) -> Inbound:
    params = {"channel": "telegram", "chat_id": "7", "user_id": "7", "text": text}
    params.update(overrides)
    return Inbound(**params)


def _set_owner(monkeypatch, owner_ids=(7,)):
    monkeypatch.setattr(agent_runtime.settings, "TELEGRAM_OWNER_IDS", ",".join(map(str, owner_ids)))


async def _zero_cost():
    return 0.0


async def test_owner_confirmation_roundtrip(_clean_sessions, monkeypatch):
    """Write tool is staged (not executed), then runs after «да»."""
    _set_owner(monkeypatch)
    calls = []

    async def fake_chat(messages, specs):
        if not calls:
            calls.append(1)
            return {
                "content": "",
                "tool_calls": [{"id": "c1", "name": "digest_send_now", "arguments": {"period": "day"}}],
                "usage": {},
            }
        return {"content": "done", "tool_calls": [], "usage": {}}

    async def fake_tool(name, args):
        assert name == "digest_send_now"
        return {"status": "sent"}

    monkeypatch.setattr(agent_runtime, "_chat", fake_chat)
    monkeypatch.setattr(agent_runtime, "call_tool", fake_tool)
    monkeypatch.setattr(agent_runtime, "_cost_today", _zero_cost)

    first = await agent_runtime.handle_inbound(_inbound("отправь сводку"))
    assert "подтверждение" in first.lower()
    second = await agent_runtime.handle_inbound(_inbound("да"))
    assert second.startswith("Выполнено")


async def test_confirmation_cancel(_clean_sessions, monkeypatch):
    """«нет» drops the staged call and never executes the tool."""
    _set_owner(monkeypatch)

    async def fake_chat(messages, specs):
        return {
            "content": "",
            "tool_calls": [{"id": "c1", "name": "digest_send_now", "arguments": {}}],
            "usage": {},
        }

    async def boom(name, args):  # must never run
        raise AssertionError("tool must not execute on cancel")

    monkeypatch.setattr(agent_runtime, "_chat", fake_chat)
    monkeypatch.setattr(agent_runtime, "call_tool", boom)
    monkeypatch.setattr(agent_runtime, "_cost_today", _zero_cost)

    first = await agent_runtime.handle_inbound(_inbound("отправь"))
    assert "подтверждение" in first.lower()
    second = await agent_runtime.handle_inbound(_inbound("нет"))
    assert second == "Отменено."


async def test_plain_reply_no_tools(_clean_sessions, monkeypatch):
    """A normal answer without tool calls is persisted and returned as-is."""
    _set_owner(monkeypatch)

    async def fake_chat(messages, specs):
        return {"content": "Привет! Чем помочь?", "tool_calls": [], "usage": {}}

    monkeypatch.setattr(agent_runtime, "_chat", fake_chat)
    monkeypatch.setattr(agent_runtime, "_cost_today", _zero_cost)

    reply = await agent_runtime.handle_inbound(_inbound("привет"))
    assert reply == "Привет! Чем помочь?"


async def test_cost_cap_short_circuits(_clean_sessions, monkeypatch):
    """At the daily cap the agent refuses before touching sessions or the model."""
    _set_owner(monkeypatch)
    monkeypatch.setattr(agent_runtime.settings, "AGENT_DAILY_COST_LIMIT", 1.0)

    async def rich():
        return 99.0

    async def boom(messages, specs):  # must never run
        raise AssertionError("model must not be called at cap")

    monkeypatch.setattr(agent_runtime, "_cost_today", rich)
    monkeypatch.setattr(agent_runtime, "_chat", boom)

    reply = await agent_runtime.handle_inbound(_inbound("привет"))
    assert "лимит" in reply.lower()
    assert await AgentSession.objects.count() == 0


async def test_non_owner_ignored(_clean_sessions, monkeypatch):
    """Strangers get nothing and create no sessions."""
    _set_owner(monkeypatch, owner_ids=(7,))

    async def boom(messages, specs):
        raise AssertionError("model must not be called for strangers")

    monkeypatch.setattr(agent_runtime, "_chat", boom)
    reply = await agent_runtime.handle_inbound(_inbound("привет", user_id="666"))
    assert reply is None
    assert await AgentSession.objects.count() == 0


async def test_listener_sends_reply_to_same_chat(monkeypatch):
    """Listener sends the agent reply back to the originating chat."""

    async def fake_handle(inbound):
        return "pong"

    sent = []

    class FakeChannel:
        name = "telegram"

        async def send(self, chat_id, text, parse_mode=None):
            sent.append((chat_id, text))
            return {"success": True}

        async def poll(self):
            yield _inbound("ping")
            raise asyncio.CancelledError

    monkeypatch.setattr(listener_module, "_handle_safely", lambda inbound: fake_handle(inbound))
    await listener_module._consume_channel(FakeChannel())
    assert sent == [("7", "pong")]


async def test_listener_skips_none_reply(monkeypatch):
    """Non-owner (None) replies produce no outbound traffic."""

    async def fake_handle(inbound):
        return None

    sent = []

    class FakeChannel:
        name = "max"

        async def send(self, chat_id, text, parse_mode=None):
            sent.append((chat_id, text))
            return {"success": True}

        async def poll(self):
            yield _inbound("spam", channel="max", user_id="666")
            raise asyncio.CancelledError

    monkeypatch.setattr(listener_module, "_handle_safely", lambda inbound: fake_handle(inbound))
    await listener_module._consume_channel(FakeChannel())
    assert sent == []
