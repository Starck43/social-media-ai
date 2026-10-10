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

# Exercise real tenant and permission guards, never the legacy test bypass.
pytestmark = pytest.mark.tenancy


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


async def test_action_send_stays_preview_only(_bound_chats):
    """The handler refuses every attempt to publish: sending is not enabled.

    Live publication is a separate, unshipped contract — until it exists the
    handler answers `dry_run=False` with `live_action_disabled` before touching
    the ledger, so a preview can never move a row out of PENDING.
    """
    spec = get_tool("action_send")
    assert spec.parameters["properties"]["dry_run"]["enum"] == [True], "the tool may only advertise a preview"

    from app.core.permissions import permission_scope

    with permission_scope(_bound_chats["owner_user"], is_owner=True):
        result = await actions_module.action_send(action_id=1, dry_run=False)
    assert result["success"] is False
    assert result["code"] == "live_action_disabled"
    assert result["dry_run"] is False


def test_action_send_schema_matches_the_handler_signature():
    """The advertised parameters are the ones the handler accepts."""
    spec = get_tool("action_send")
    assert spec.parameters["required"] == ["action_id"]
    assert set(spec.parameters["properties"]) == {"action_id", "dry_run"}


@pytest.fixture
async def _unrelated_session():
    """A separate workspace session must survive bound-chat arrange/cleanup."""
    from app.core.tenant_context import tenant_scope
    from app.models import AgentSession
    from app.models.managers.tenant_manager import tenants

    suffix = secrets.token_hex(6)
    tenant = await tenants.create(name="Registry sentinel", slug=f"registry-sentinel-{suffix}", plan="business")
    session = None
    try:
        with tenant_scope(tenant.id):
            session = await AgentSession.objects.create(
                channel="telegram", chat_id=f"registry-sentinel-{suffix}", kind="private",
                is_active=True, state={"sentinel": suffix},
            )
        yield {"tenant_id": tenant.id, "session_id": session.id, "state": {"sentinel": suffix}}
        with tenant_scope(tenant.id):
            preserved = await AgentSession.objects.get(id=session.id)
            assert preserved is not None, "registry cleanup deleted another workspace session"
            assert preserved.state == {"sentinel": suffix}
    finally:
        with tenant_scope(tenant.id):
            await tenants.delete_by_id(tenant.id)


@pytest.fixture
async def _bound_chats(_unrelated_session):
    """Fresh tenant/chats/users and explicit roles: no-right member, view-only owner.

    Ownership comes solely from the SUPERUSER membership. The owner's platform
    User has only botaction.view, not a superuser role. Seeded roles are unchanged.
    Teardown touches only this fixture's tenant, users and throwaway roles.
    """
    from app.core.tenant_context import tenant_scope
    from app.models import Permission, Role, User
    from app.models.managers.tenant_manager import tenant_channels, tenant_users, tenants
    from app.types import ActionType, UserRoleType

    suffix = secrets.token_hex(6)
    tenant = None
    users = []
    roles = []
    try:
        permissions = await Permission.objects.prefetch_related("model_type").filter(action_type=ActionType.VIEW)
        permission = next((row for row in permissions if row.model_type
                           and row.model_type.model_name.lower() == "botaction"), None)
        assert permission is not None, "botaction.view must be seeded"
        owner_membership_role = await Role.objects.get(codename=UserRoleType.SUPERUSER.name)
        assert owner_membership_role is not None
        for label, permission_ids in (("member", []), ("owner", [permission.id])):
            role = await Role.objects.create_role(
                name=f"registry-{label}-{suffix}", codename=UserRoleType.VIEWER.name,
            )
            roles.append(role)
            await Role.objects.set_permissions(role.id, permission_ids)
            user = await User.objects.create_user(
                username=f"registry-{label}-{suffix}", email=f"registry-{label}-{suffix}@example.com",
                password="secret-password-1", role_id=role.id, is_superuser=False,
            )
            users.append(user)
        viewer, owner = users
        tenant = await tenants.create(name="Registry boundary", slug=f"registry-{suffix}", plan="business")
        viewer_chat, owner_chat = f"registry-member-{suffix}", f"registry-owner-{suffix}"
        with tenant_scope(tenant.id):
            for user, chat, role_id in (
                (viewer, viewer_chat, roles[0].id), (owner, owner_chat, owner_membership_role.id),
            ):
                member = await tenant_users.add_member(
                    tenant_id=tenant.id, channel="telegram", external_user_id=chat, role_id=role_id,
                )
                assert member is not None
                await tenant_users.update_by_id(member.id, user_id=user.id)
                binding = await tenant_channels.bind(
                    tenant_id=tenant.id, channel="telegram", chat_id=chat, kind="private",
                )
                assert binding is not None and binding.tenant_id == tenant.id
            viewer_user = await User.objects.prefetch_related("role.permissions.model_type").get(id=viewer.id)
            owner_user = await User.objects.prefetch_related("role.permissions.model_type").get(id=owner.id)
            assert not viewer_user.has_perm_for("botaction", ActionType.VIEW)
            assert owner_user.has_perm_for("botaction", ActionType.VIEW)
            assert not owner_user.has_perm_for("botaction", ActionType.UPDATE)
            assert not owner_user.is_superuser and not owner_user._is_superuser_role()
            yield {
                "tenant_id": tenant.id, "viewer_id": viewer.id, "owner_id": owner.id,
                "viewer_chat": viewer_chat, "owner_chat": owner_chat, "owner_user": owner_user,
                "unrelated": _unrelated_session,
            }
    finally:
        if tenant is not None:
            with tenant_scope(tenant.id):
                await tenants.delete_by_id(tenant.id)
        for user in reversed(users):
            await User.objects.delete_user(user.id)
        for role in reversed(roles):
            await Role.objects.set_permissions(role.id, [])
            await Role.objects.delete_by_id(role.id)


async def test_registry_fixture_keeps_other_workspace_session(_bound_chats):
    """Arrange cannot delete other chats; sentinel teardown also checks cleanup."""
    from app.core.permissions import get_current_user, has_permission
    from app.core.tenant_context import current_tenant_id, is_bypass, tenant_scope
    from app.models import AgentSession

    assert not is_bypass()
    assert current_tenant_id() == _bound_chats["tenant_id"]
    assert get_current_user() is None
    assert not has_permission(None, "botaction", "view"), "no inherited operator grant"
    foreign = _bound_chats["unrelated"]
    assert await AgentSession.objects.get(id=foreign["session_id"]) is None
    with tenant_scope(foreign["tenant_id"]):
        preserved = await AgentSession.objects.get(id=foreign["session_id"])
        assert preserved is not None and preserved.state == foreign["state"]


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

    await agent_runtime.handle_inbound(
        _inbound("отправь действие 1", chat_id=_bound_chats["viewer_chat"], user_id=_bound_chats["viewer_chat"])
    )
    assert dispatched == [], "the gated tool must never reach the handler"

    session = await AgentSession.objects.filter(chat_id=_bound_chats["viewer_chat"]).first()
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

    first = await agent_runtime.handle_inbound(
        _inbound("отправь действие 1", chat_id=_bound_chats["owner_chat"], user_id=_bound_chats["owner_chat"])
    )
    assert "подтверждение" in first.lower()
    assert dispatched == [], "a confirmed write must not run on the first message"

    session = await AgentSession.objects.filter(chat_id=_bound_chats["owner_chat"]).first()
    assert session is not None
    pending = (session.state or {}).get("pending_confirmation")
    assert pending is not None and pending["name"] == "action_send"
    assert pending["required_permission"] == "botaction.view"
    # A staged intent is actor-bound: another chat cannot consume it.
    assert pending["authorization"]["actor"]["chat_id"] == _bound_chats["owner_chat"]


async def test_expired_confirmation_is_refused(_bound_chats, monkeypatch):
    """A staged «да» whose intent expired is dropped, never executed.

    The intent is built by the real staging path and then aged past its
    deadline, so the refusal is specifically the expiry and not a malformed
    payload: the permission re-check at confirmation time only has teeth
    while the scope that sets the user is still open.
    """
    from datetime import datetime, timedelta, timezone

    from app.agent.confirmation import make_pending_intent, pending_rejection
    from app.agent.identity import resolve_runtime_identity
    from app.core.tenant_context import tenant_scope
    from app.models import AgentSession
    from app.models.managers.agent_session_manager import agent_sessions
    from app.services.tenancy.resolver import Resolution

    dispatched: list = []
    _stub_loop(monkeypatch, dispatched, repeat=False)

    session = await agent_sessions.get_or_create(
        channel="telegram", chat_id=_bound_chats["owner_chat"], kind="private", is_owner=True,
    )
    with tenant_scope(session.tenant_id):
        identity = await resolve_runtime_identity(
            Resolution(
                tenant_id=session.tenant_id, channel="telegram", chat_id=_bound_chats["owner_chat"],
                user_id=_bound_chats["owner_chat"],
            )
        )
    assert identity is not None, "the owner chat must resolve a bound identity"
    spec = agent_runtime.TOOL_REGISTRY["action_send"]
    intent = make_pending_intent(identity, session, spec, {"action_id": 1}, "call-1")
    assert intent is not None, "a confirm tool must be stageable"
    assert intent["required_permission"] == "botaction.view"
    assert pending_rejection(intent, identity, session, spec) is None
    intent["expires_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert pending_rejection(intent, identity, session, spec) == "expired_intent"
    await agent_runtime._set_pending(session, intent)

    await agent_runtime.handle_inbound(
        _inbound("да", chat_id=_bound_chats["owner_chat"], user_id=_bound_chats["owner_chat"])
    )
    assert dispatched == [], "an expired confirmation must not execute"

    still = await AgentSession.objects.filter(chat_id=_bound_chats["owner_chat"]).first()
    assert (still.state or {}).get("pending_confirmation") is None, "an expired intent must be dropped"
