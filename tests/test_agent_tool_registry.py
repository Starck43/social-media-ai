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

`action_send` is a *preview*-only tool in this runtime: it never publishes, so
the right it declares is the read right (`botaction.view`), not
`botaction.update`. Enabling live publishing is a separate contract; these
tests pin the current one and must not be relaxed when that lands.
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
    """A preview is a read, but it is still gated and never auto-executed."""
    spec = get_tool("action_send")
    assert spec.required_permission == "botaction.view"
    assert spec.confirm is True


async def test_action_send_stays_preview_only():
    """The handler refuses every attempt to publish: sending is not enabled.

    Live publication is a separate, unshipped contract — until it exists the
    handler answers `dry_run=False` with `live_action_disabled` before touching
    the ledger, so a preview can never move a row out of PENDING.
    """
    spec = get_tool("action_send")
    assert spec.parameters["properties"]["dry_run"]["enum"] == [True], "the tool may only advertise a preview"

    result = await actions_module.action_send(action_id=1, dry_run=False)  # noqa: FBT003 - the forbidden call on purpose
    assert result["success"] is False
    assert result["code"] == "live_action_disabled"
    assert result["dry_run"] is False


def test_action_send_schema_matches_the_handler_signature():
    """The advertised parameters are the ones the handler accepts."""
    spec = get_tool("action_send")
    assert spec.parameters["required"] == ["action_id"]
    assert set(spec.parameters["properties"]) == {"action_id", "dry_run"}


@pytest.fixture
async def _bound_chats():
    """Two isolated chats in the owner workspace: a real VIEWER and an owner.

    Both members are linked to a `users` row — without that link the runtime
    resolves no identity at all (there is no legacy pass-through any more), and
    the viewer carries the VIEWER role, which holds no botaction right, so the
    gate test is not vacuous. Owner-ness comes from the membership role
    (SUPERUSER), never from an env allowlist, and the two chats are separate
    sessions: nothing one chat stages is visible to the other. The fixture
    removes only the rows it created.
    """
    from app.core.config import settings
    from app.core.tenant_context import tenant_scope
    from app.models import AgentSession, Role, User
    from app.models.managers.tenant_manager import tenant_channels, tenant_users, tenants

    await AgentSession.objects.delete()
    tenant = await tenants.get_or_create_owner(settings.DEFAULT_TENANT_SLUG)

    viewer_role = await Role.objects.get(codename="VIEWER")
    owner_role = await Role.objects.get(codename="SUPERUSER")
    suffix = secrets.token_hex(3)
    viewer = await User.objects.create_user(
        username=f"toolviewer{suffix}",
        email=f"toolviewer{suffix}@example.com",
        password="secret-password-1",
        role_id=viewer_role.id,
        is_superuser=False,
    )
    owner = await User.objects.create_user(
        username=f"toolowner{suffix}",
        email=f"toolowner{suffix}@example.com",
        password="secret-password-1",
        role_id=owner_role.id,
        is_superuser=False,
    )
    with tenant_scope(bypass=True):
        member = await tenant_users.add_member(
            tenant_id=tenant.id, channel="telegram", external_user_id="8", role_id=viewer_role.id
        )
        await tenant_users.update_by_id(member.id, user_id=viewer.id)
        owner_member = await tenant_users.add_member(
            tenant_id=tenant.id, channel="telegram", external_user_id="9", role="owner"
        )
        await tenant_users.update_by_id(owner_member.id, user_id=owner.id)
        chat_8 = await tenant_channels.bind(tenant_id=tenant.id, channel="telegram", chat_id="8", kind="private")
        chat_9 = await tenant_channels.bind(tenant_id=tenant.id, channel="telegram", chat_id="9", kind="private")

    assert member is not None and owner_member is not None
    assert chat_8 is not None and chat_9 is not None
    assert chat_8.tenant_id == chat_9.tenant_id == tenant.id
    assert owner_member.role_id == owner_role.id, "owner-ness is the membership role, not an env allowlist"

    yield {"tenant_id": tenant.id, "viewer_id": viewer.id, "owner_id": owner.id}

    await AgentSession.objects.delete()
    await User.objects.delete_user(viewer.id)
    await User.objects.delete_user(owner.id)
    with tenant_scope(bypass=True):
        await tenant_channels.delete_by_id(chat_8.id)
        await tenant_channels.delete_by_id(chat_9.id)
        await tenant_users.delete_by_id(owner_member.id)
        await tenant_users.delete_by_id(member.id)


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
    """A member without the declared botaction right is refused before dispatch.

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
    """The owner passes the gate and gets the confirmation, not the send.

    Same loop, owner chat: the write is staged behind «да» and the staged
    payload carries the permission, so the confirmation re-checks it — the
    contract that must survive the rebinding. Owner-ness here comes from the
    membership role, not from an env allowlist.
    """
    from app.models import AgentSession

    dispatched: list = []
    _stub_loop(monkeypatch, dispatched)

    first = await agent_runtime.handle_inbound(_inbound("отправь действие 1", chat_id="9", user_id="9"))
    assert "подтверждение" in first.lower()
    assert dispatched == [], "a confirmed write must not run on the first message"

    session = await AgentSession.objects.filter(chat_id="9").first()
    assert session is not None
    pending = (session.state or {}).get("pending_confirmation")
    assert pending is not None and pending["name"] == "action_send"
    assert pending["required_permission"] == "botaction.view"
    # A staged intent is actor-bound: another chat cannot consume it.
    assert pending["authorization"]["actor"]["chat_id"] == "9"


async def test_expired_confirmation_is_refused(_bound_chats, monkeypatch):
    """A staged «да» whose intent expired is dropped, never executed.

    The intent is built by the real staging path and then aged past its
    deadline, so the refusal is specifically the expiry and not a malformed
    payload: the permission re-check at confirmation time only has teeth
    while the scope that sets the user is still open.
    """
    from datetime import datetime, timedelta, timezone

    from app.agent.confirmation import make_pending_intent
    from app.agent.identity import resolve_runtime_identity
    from app.core.tenant_context import tenant_scope
    from app.models import AgentSession
    from app.models.managers.agent_session_manager import agent_sessions
    from app.services.tenancy.resolver import Resolution

    dispatched: list = []
    _stub_loop(monkeypatch, dispatched, repeat=False)

    session = await agent_sessions.get_or_create(channel="telegram", chat_id="9", kind="private", is_owner=True)
    with tenant_scope(session.tenant_id):
        identity = await resolve_runtime_identity(
            Resolution(tenant_id=session.tenant_id, channel="telegram", chat_id="9", user_id="9")
        )
    assert identity is not None, "the owner chat must resolve a bound identity"
    spec = agent_runtime.TOOL_REGISTRY["action_send"]
    intent = make_pending_intent(identity, session, spec, {"action_id": 1}, "call-1")
    assert intent is not None, "a confirm tool must be stageable"
    assert intent["required_permission"] == "botaction.view"
    intent["expires_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    await agent_runtime._set_pending(session, intent)

    await agent_runtime.handle_inbound(_inbound("да", chat_id="9", user_id="9"))
    assert dispatched == [], "an expired confirmation must not execute"

    still = await AgentSession.objects.filter(chat_id="9").first()
    assert (still.state or {}).get("pending_confirmation") is None, "an expired intent must be dropped"
