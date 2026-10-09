"""Registry binding and permission gate for the agent's write tools.

The `@tool` decorator registers whatever function it decorates under the
tool's name, so a decorator left on the wrong function silently rebinds the
whole tool: the registry dispatches to a handler that does something else
entirely. That is exactly what happened to `action_send` — its decorator
sat on the `_auto_actions_forced_dry_run` helper, so every «action_send»
call reached a no-argument function returning a reason string and no action
was ever sent.

These tests assert the binding through the registry (the only path the
runtime dispatches on), not by calling the handler directly, and drive the
real loop for the permission gate.
"""

import secrets

import pytest

from app.agent import runtime as agent_runtime
from app.agent.tools import TOOL_REGISTRY, get_tool
from app.agent.toolset import actions as actions_module


def test_action_send_dispatches_to_the_real_handler():
    """The registry must map the tool to the function that sends the action."""
    spec = get_tool("action_send")
    assert spec is not None, "action_send must be registered"
    assert spec.handler is actions_module.action_send
    assert spec.handler is not actions_module._auto_actions_forced_dry_run


def test_the_forced_dry_run_helper_is_not_a_tool():
    """A private helper must never be reachable as a tool."""
    for name, spec in TOOL_REGISTRY.items():
        assert spec.handler is not actions_module._auto_actions_forced_dry_run, f"helper registered as tool {name!r}"


def test_action_send_declares_permission_and_confirmation():
    """Publishing is a write: gated on botaction.update, never auto-executed."""
    spec = get_tool("action_send")
    assert spec.required_permission == "botaction.update"
    assert spec.confirm is True


def test_action_send_schema_matches_the_handler_signature():
    """The advertised parameters are the ones the handler accepts."""
    spec = get_tool("action_send")
    assert spec.parameters["required"] == ["action_id"]
    assert set(spec.parameters["properties"]) == {"action_id", "dry_run"}


@pytest.fixture
async def _bound_chats():
    """Two chats in the owner workspace: a real VIEWER and a plain owner.

    The viewer is linked to a `users` row carrying the VIEWER role — without
    that link the runtime resolves no user and every permission check
    passes through as legacy, which would make the gate test vacuous.
    """
    from app.core.config import settings
    from app.core.tenant_context import tenant_scope
    from app.models import AgentSession, Role, User
    from app.models.managers.tenant_manager import tenant_channels, tenant_users, tenants

    await AgentSession.objects.delete()
    tenant = await tenants.get_or_create_owner(settings.DEFAULT_TENANT_SLUG)

    role = await Role.objects.get(codename="VIEWER")
    suffix = secrets.token_hex(3)
    viewer = await User.objects.create_user(
        username=f"toolviewer{suffix}",
        email=f"toolviewer{suffix}@example.com",
        password="secret-password-1",
        role_id=role.id,
        is_superuser=False,
    )
    with tenant_scope(bypass=True):
        member = await tenant_users.add_member(
            tenant_id=tenant.id, channel="telegram", external_user_id="8", role_id=role.id
        )
        await tenant_users.update_by_id(member.id, user_id=viewer.id)
        await tenant_users.add_member(tenant_id=tenant.id, channel="telegram", external_user_id="9", role="owner")
        await tenant_channels.bind(tenant_id=tenant.id, channel="telegram", chat_id="8", kind="private")
        await tenant_channels.bind(tenant_id=tenant.id, channel="telegram", chat_id="9", kind="private")

    yield {"tenant_id": tenant.id, "viewer_id": viewer.id}
    await AgentSession.objects.delete()
    with tenant_scope(bypass=True):
        await User.objects.delete_user(viewer.id)


def _inbound(text: str, chat_id: str, user_id: str):
    from app.channels.base import Inbound

    return Inbound(channel="telegram", chat_id=chat_id, user_id=user_id, text=text)


async def _zero_cost():
    return 0.0


def _stub_loop(monkeypatch, dispatched: list, *, repeat: bool = True):
    """The model asks for «action_send»; dispatch is recorded, never real.

    `repeat=False` asks once and then answers plainly, like a model that read
    the tool result — without it the loop re-asks until the iteration cap and
    the refusal never surfaces in the reply.
    """

    async def fake_chat(messages, specs):
        asked = any(m.get("role") == "tool" for m in messages)
        if repeat or not asked:
            return {
                "content": "",
                "tool_calls": [{"id": "c1", "name": "action_send", "arguments": {"action_id": 1}}],
                "usage": {},
            }
        return {"content": "Готово.", "tool_calls": [], "usage": {}}

    async def record(name, args):
        dispatched.append((name, args))
        return {"success": True}

    monkeypatch.setattr(agent_runtime, "_chat", fake_chat)
    monkeypatch.setattr(agent_runtime, "call_tool", record)
    monkeypatch.setattr(agent_runtime, "_cost_today", _zero_cost)


async def test_viewer_cannot_execute_action_send(_bound_chats, monkeypatch):
    """A member without botaction.update is refused before any dispatch.

    Drives the real loop: the model asks for «action_send», the gate answers
    with the refusal, and the handler is never called. The refusal is what
    reaches the model as the tool result.
    """
    from app.models import AgentMessage, AgentSession

    dispatched: list = []
    _stub_loop(monkeypatch, dispatched, repeat=False)

    await agent_runtime.handle_inbound(_inbound("отправь действие 1", chat_id="8", user_id="8"))
    assert dispatched == [], "the gated tool must never reach the handler"

    session = await AgentSession.objects.filter(chat_id="8").first()
    assert session is not None
    tool_msg = await AgentMessage.objects.filter(session_id=session.id, role="tool").first()
    assert tool_msg is not None and "нет прав" in (tool_msg.content or "")


async def test_owner_action_send_is_staged_for_confirmation(_bound_chats, monkeypatch):
    """The owner passes the gate and gets the preview, not the send.

    Same loop, owner chat: the write is staged behind «да» and the staged
    payload carries the permission, so the confirmation re-checks it — the
    contract UX-02 requires to survive the rebinding.
    """
    from app.models import AgentSession

    monkeypatch.setattr(agent_runtime.settings, "TELEGRAM_OWNER_IDS", "9")
    dispatched: list = []
    _stub_loop(monkeypatch, dispatched)

    first = await agent_runtime.handle_inbound(_inbound("отправь действие 1", chat_id="9", user_id="9"))
    assert "подтверждение" in first.lower()
    assert dispatched == [], "a confirmed write must not run on the first message"

    session = await AgentSession.objects.filter(chat_id="9").first()
    assert session is not None
    pending = (session.state or {}).get("pending_confirmation")
    assert pending is not None and pending["name"] == "action_send"
    assert pending["required_permission"] == "botaction.update"


async def test_expired_confirmation_is_refused(_bound_chats, monkeypatch):
    """A staged «да» that expired is dropped, never executed.

    The expiry lives in `_pending_confirmation`; the permission re-check at
    confirmation time only has teeth while the scope that sets the user is
    still open, which is what the runtime indentation repair restored.
    """
    from datetime import datetime, timedelta, timezone

    from app.models.managers.agent_session_manager import agent_sessions

    monkeypatch.setattr(agent_runtime.settings, "TELEGRAM_OWNER_IDS", "9")
    dispatched: list = []
    _stub_loop(monkeypatch, dispatched, repeat=False)

    session = await agent_sessions.get_or_create(channel="telegram", chat_id="9", kind="private", is_owner=True)
    expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    await agent_runtime._set_pending(
        session,
        {
            "name": "action_send",
            "args": {"action_id": 1},
            "expires_at": expired,
            "required_permission": "botaction.update",
        },
    )

    await agent_runtime.handle_inbound(_inbound("да", chat_id="9", user_id="9"))
    assert dispatched == [], "an expired confirmation must not execute"
