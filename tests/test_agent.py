"""Agent chat-loop tests: runtime confirmation flow + channel listener.

Covers what the runtime depends on and nothing executes against real LLM or
HTTP: `agent.runtime._chat` is stubbed, so tests exercise only our loop —
confirmation gating, tool dispatch, cost-cap short-circuit, owner allowlist —
not the model. Listener tests stub `handle_inbound` the same way.

Uses the real database (like test_digest_e2e.py) with fixture-level cleanup of
sessions created by the tests.
"""

import asyncio
import json

import httpx
import pytest

from app.agent import runtime as agent_runtime
from app.channels import listener as listener_module
from app.channels.base import Inbound
from app.models import AgentMessage, AgentSession, DigestRun


@pytest.fixture
async def _clean_sessions():
    """Reset agent state, with the test chat already bound to the workspace.

    Every test in this file drives the agent loop, not first-contact
    onboarding: on an unbound chat the first message only binds it and
    returns a welcome line instead of reaching the model. Binding chat 7 here
    keeps the tests independent of whatever a previous run left behind.
    """
    from app.core.config import settings
    from app.core.tenant_context import tenant_scope
    from app.models.managers.tenant_manager import tenant_channels, tenant_users, tenants

    await AgentSession.objects.delete()
    tenant = await tenants.get_or_create_owner(settings.DEFAULT_TENANT_SLUG)
    with tenant_scope(bypass=True):
        await tenant_users.add_member(tenant_id=tenant.id, channel="telegram", external_user_id="7", role="owner")
        await tenant_channels.bind(tenant_id=tenant.id, channel="telegram", chat_id="7", kind="private")
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


async def test_scenario_create_waits_for_confirmation(_clean_sessions, monkeypatch):
    """A scenario is never written on the first message — only after «да».

    The chat agent creates scenarios from what the owner says, so the staging is
    the only thing standing between a misheard wish and a saved row. This drives
    the real loop (no stubbed `call_tool`) and checks both halves: the first
    message writes nothing, the second writes the scenario.
    """
    from app.models import AgentScenario

    _set_owner(monkeypatch)
    before = await AgentScenario.objects.count()

    async def fake_chat(messages, specs):
        return {
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "name": "scenario_create",
                    "arguments": {"name": "Бренд из чата", "template_key": "brand_monitoring"},
                }
            ],
            "usage": {},
        }

    monkeypatch.setattr(agent_runtime, "_chat", fake_chat)
    monkeypatch.setattr(agent_runtime, "_cost_today", _zero_cost)

    first = await agent_runtime.handle_inbound(_inbound("сделай сценарий по бренду"))
    assert "подтверждение" in first.lower()
    assert await AgentScenario.objects.count() == before, "nothing may be written before «да»"

    second = await agent_runtime.handle_inbound(_inbound("да"))
    assert second.startswith("Выполнено")
    created = await AgentScenario.objects.filter(name="Бренд из чата").first()
    assert created is not None, "the confirmed call must create the scenario"
    assert "brand_mentions" in created.analysis_types, "the preset must have been expanded"

    await AgentScenario.objects.delete(id=created.id)


async def test_plain_reply_no_tools(_clean_sessions, monkeypatch):
    """A normal answer without tool calls is persisted and returned as-is."""
    _set_owner(monkeypatch)

    async def fake_chat(messages, specs):
        return {"content": "Привет! Чем помочь?", "tool_calls": [], "usage": {}}

    monkeypatch.setattr(agent_runtime, "_chat", fake_chat)
    monkeypatch.setattr(agent_runtime, "_cost_today", _zero_cost)

    reply = await agent_runtime.handle_inbound(_inbound("привет"))
    assert reply == "Привет! Чем помочь?"


async def test_a_tool_call_is_replayed_to_the_api_in_wire_shape(_clean_sessions, monkeypatch):
    """The assistant turn that asked for a tool must go back in API shape.

    The loop dispatches on a flat call (`{"id", "name", "arguments"}` with a
    dict) and stores exactly that, but the *next* request to the provider has to
    carry the wire shape: `arguments` nested under `function` and serialized to a
    JSON string. Replaying the flat one made the provider reject the request
    with `tool_calls[0].function must be an object`, and the user saw a bare
    "Ошибка LLM: 400" at the end of every turn that used a tool.

    The second `_chat` call is the one that matters: it is the request carrying
    the assistant's tool_calls back.
    """
    _set_owner(monkeypatch)
    seen: list[list[dict]] = []

    async def fake_chat(messages, specs):
        seen.append(messages)
        if len(seen) == 1:
            return {
                "content": "",
                "tool_calls": [{"id": "c1", "name": "system_status", "arguments": {}}],
                "usage": {},
            }
        return {"content": "Готово", "tool_calls": [], "usage": {}}

    async def fake_tool(name, args):
        return {"ok": True}

    monkeypatch.setattr(agent_runtime, "_chat", fake_chat)
    monkeypatch.setattr(agent_runtime, "call_tool", fake_tool)
    monkeypatch.setattr(agent_runtime, "_cost_today", _zero_cost)

    reply = await agent_runtime.handle_inbound(_inbound("статус"))
    assert reply == "Готово", "the turn must complete instead of erroring"
    assert len(seen) == 2, "the tool round-trip must have taken a second request"

    assistant = [m for m in seen[1] if m["role"] == "assistant" and m.get("tool_calls")]
    assert len(assistant) == 1, f"the replayed assistant turn is missing: {seen[1]!r}"

    call = assistant[0]["tool_calls"][0]
    assert call["type"] == "function"
    assert call["id"] == "c1", "the tool result is matched by id, so it must survive"
    fn = call["function"]
    assert isinstance(fn, dict), f"function must be an object, got {fn!r}"
    assert fn["name"] == "system_status"
    # A JSON string, not a dict — and it must round-trip back to the arguments.
    assert isinstance(fn["arguments"], str), f"arguments must be a JSON string, got {fn['arguments']!r}"
    assert json.loads(fn["arguments"]) == {}


async def test_llm_failures_reply_short_and_never_leak_the_body(_clean_sessions, monkeypatch):
    """The chat shows a readable line; the provider's body stays in the log.

    `httpx.HTTPStatusError` stringifies to `<status>: <response text>`, and a
    provider's text is a JSON error document. Pasting that into the transcript
    buried the conversation in English JSON, so the reply keeps only the status
    (the part that says what to do) and points at the logs.
    """
    _set_owner(monkeypatch)
    body = '{"error":{"message":"messages[9].tool_calls[0].function must be an object"}}'
    cases = {
        400: "отклонила",
        401: "API-ключ",
        429: "лимита запросов",
        503: "недоступна",
    }

    for status, expected in cases.items():
        response = httpx.Response(status, text=body, request=httpx.Request("POST", "http://llm/chat/completions"))
        error = httpx.HTTPStatusError(f"{status}: {body}", request=response.request, response=response)

        async def failing(messages, specs, _err=error):
            raise _err

        monkeypatch.setattr(agent_runtime, "_chat", failing)
        monkeypatch.setattr(agent_runtime, "_cost_today", _zero_cost)

        reply = await agent_runtime.handle_inbound(_inbound(f"вопрос-{status}"))
        assert reply is not None
        assert expected in reply, f"status {status}: {reply!r}"
        assert body not in reply, f"status {status}: the provider body leaked into the chat"
        assert "{" not in reply and "}" not in reply, f"status {status}: JSON leaked: {reply!r}"

    # A transport failure has no status at all and must still read as a sentence.
    async def offline(messages, specs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(agent_runtime, "_chat", offline)
    reply = await agent_runtime.handle_inbound(_inbound("офлайн"))
    assert reply == "Не удалось обратиться к языковой модели. Подробности в логах."


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


async def test_cost_cap_enforced_from_recorded_usage(_clean_sessions, monkeypatch):
    """usage['cost'] flows to the DB, so the next message trips the real cap.

    Unlike test_cost_cap_short_circuits nothing is stubbed in the cost path:
    record_usage -> cost_today() -> daily_cost_today() must fire on their own
    (this chain used to sum to 0 forever because chat usage carried no cost).
    """
    _set_owner(monkeypatch)

    async def priced(messages, specs):
        return {"content": "ok", "tool_calls": [], "usage": {"total_tokens": 100, "cost": 6.0}}

    monkeypatch.setattr(agent_runtime, "_chat", priced)

    # Start from a zero spend metric regardless of what earlier tests recorded.
    await AgentMessage.objects.filter().delete()
    await DigestRun.objects.filter().delete()

    # Under the cap: no spend recorded yet, the reply goes through.
    first = await agent_runtime.handle_inbound(_inbound("привет"))
    assert first == "ok"

    # The recorded $6 exceeds the default $5 workspace cap -> refused.
    second = await agent_runtime.handle_inbound(_inbound("снова"))
    assert "лимит" in second.lower()


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


async def test_help_command(_clean_sessions, monkeypatch):
    """/help returns the help text without ever calling the model."""
    _set_owner(monkeypatch)

    async def boom(messages, specs):  # must never run
        raise AssertionError("model must not be called for /help")

    monkeypatch.setattr(agent_runtime, "_chat", boom)
    monkeypatch.setattr(agent_runtime, "_cost_today", _zero_cost)

    from app.agent.prompts import AGENT_HELP_TEXT

    reply = await agent_runtime.handle_inbound(_inbound("/help"))
    assert reply == AGENT_HELP_TEXT
    assert "/stop" in reply


async def test_chat_uses_agent_limits(_clean_sessions, monkeypatch):
    """_chat passes AGENT_MAX_TOKENS/AGENT_TEMPERATURE to the LLM client."""
    captured = {}

    async def _fake_fallback(messages, tools=None, **kwargs):
        captured.update(kwargs)
        return {"content": "ok", "tool_calls": [], "usage": {}}

    monkeypatch.setattr("app.services.ai.llm_client.chat_with_fallback", _fake_fallback)
    monkeypatch.setattr(agent_runtime.settings, "AGENT_MAX_TOKENS", 2048)
    monkeypatch.setattr(agent_runtime.settings, "AGENT_TEMPERATURE", 0.5)
    _set_owner(monkeypatch)
    monkeypatch.setattr(agent_runtime, "_cost_today", _zero_cost)

    first = await agent_runtime.handle_inbound(_inbound("привет", user_id="7"))
    assert first == "ok"
    assert captured.get("max_tokens") == 2048
    assert captured.get("temperature") == 0.5


async def test_confirmed_tool_resumes_the_plan(_clean_sessions, monkeypatch):
    """«да» no longer ends the turn: the agent continues its multi-step plan.

    The reported failure: the owner asked for a task, confirmed `source_add`,
    and the agent stopped — the task was only created after a follow-up
    «а где сама задача?». After «да» the loop must resume so the plan
    (create source -> create task) continues on its own.
    """
    _set_owner(monkeypatch)
    calls = []

    async def fake_chat(messages, specs):
        calls.append(1)
        if len(calls) == 1:
            return {
                "content": "Создаю источник, затем задачу.",
                "tool_calls": [
                    {
                        "id": "c1",
                        "name": "source_add",
                        "arguments": {
                            "platform": "vk",
                            "source_type": "user",
                            "external_id": "annastrizhovadesign",
                            "name": "Анна Стрижова",
                        },
                    }
                ],
                "usage": {},
            }
        return {
            "content": "Теперь создаю задачу.",
            "tool_calls": [
                {
                    "id": "c2",
                    "name": "task_add",
                    "arguments": {
                        "name": "Слежение за Анной",
                        "cron": "0 21 * * 1",
                        "job_type": "collect",
                        "scenario_id": 8,
                        "source_ids": [2939],
                        "start_date": "2026-07-01",
                    },
                }
            ],
            "usage": {},
        }

    async def fake_tool(name, args):
        if name == "source_add":
            return {"status": "created", "source_id": 2939, "name": "Анна Стрижова"}
        if name == "task_add":
            return {"status": "created", "name": args["name"], "next_run_at": "2026-10-12T18:00:00+00:00"}
        raise AssertionError(f"unexpected tool {name}")

    monkeypatch.setattr(agent_runtime, "_chat", fake_chat)
    monkeypatch.setattr(agent_runtime, "call_tool", fake_tool)
    monkeypatch.setattr(agent_runtime, "_cost_today", _zero_cost)

    first = await agent_runtime.handle_inbound(_inbound("создай задачу для слежения за Анной"))
    assert "подтверждение" in first.lower()
    assert "источник" in first.lower()
    assert "{" not in first and "}" not in first, f"raw JSON leaked into the preview: {first!r}"

    # «да» executes source_add AND immediately asks for the next step — the
    # owner does not have to nudge with «а где сама задача?».
    second = await agent_runtime.handle_inbound(_inbound("да"))
    assert second.startswith("Выполнено"), second
    assert "подтверждение" in second.lower(), second
    assert "задачу" in second.lower(), second
    assert "0 21" not in second, f"cron must read as human text, not as an expression: {second!r}"
    assert "Еженедельно" in second, f"cron must read as human text: {second!r}"

    # The staged tool message must now carry the real result, not the
    # «Требуется подтверждение» text the model would have replayed.
    session = await AgentSession.objects.get(channel="telegram", chat_id="7")
    tool_rows = [
        m.content
        for m in await AgentMessage.objects.filter(session_id=session.id, role="tool").all()
        if m.tool_name == "c1"
    ]
    assert tool_rows, "the staged tool message must exist"
    assert "source_id" in tool_rows[-1], f"the tool result must overwrite the confirmation text: {tool_rows[-1]!r}"
    assert "Требуется подтверждение" not in tool_rows[-1]

    # A second «да» runs task_add — the staged next step fires.
    third = await agent_runtime.handle_inbound(_inbound("да"))
    assert third.startswith("Выполнено"), third
    assert "Слежение за Анной" in third, third


async def test_confirmation_previews_are_human(_clean_sessions):
    """Write-tool confirmations read as sentences, never as JSON dumps."""
    from app.agent.runtime import _human_confirmation

    source = _human_confirmation(
        "source_add",
        {"platform": "vk", "source_type": "user", "external_id": "annastrizhovadesign", "name": "Анна Стрижова"},
    )
    assert "добавить источник «Анна Стрижова»" in source
    assert "ВКонтакте" in source
    assert "пользователь" in source
    assert "{" not in source and "}" not in source

    task = _human_confirmation(
        "task_add",
        {"name": "Слежение за Анной", "cron": "0 21 * * 1", "job_type": "collect", "start_date": "2026-07-01"},
    )
    assert "создать задачу «Слежение за Анной»" in task
    assert "сбор контента" in task
    assert "Еженедельно" in task
    assert "0 21" not in task and "{" not in task

    cancel = _human_confirmation("task_remove", {"name": "Слежение за Анной"})
    assert "удалить задачу «Слежение за Анной»" in cancel


async def _noop_async(*args, **kwargs):
    return object()
