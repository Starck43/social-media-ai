from types import SimpleNamespace

import pytest

from app.agent.confirmation import make_pending_intent, pending_rejection
from app.agent.identity import RuntimeIdentity, current_memory_actor, memory_actor_scope
from app.core.tenant_context import TenantContextError, tenant_scope
from app.models.managers.agent_memory_manager import agent_memory


def test_memory_actor_scope_is_required_and_restored():
    with tenant_scope(71):
        with pytest.raises(TenantContextError):
            agent_memory._actor()

        with memory_actor_scope(19, False):
            assert current_memory_actor() == (19, False)
            assert agent_memory._actor() == (19, False, 71)

            with memory_actor_scope(20, True):
                assert agent_memory._actor() == (20, True, 71)

            assert agent_memory._actor() == (19, False, 71)

        assert current_memory_actor() is None


def test_approval_binding_changes_when_workspace_role_or_ai_access_changes():
    base = dict(
        tenant_id=71,
        user_id=19,
        membership_id=29,
        channel="web",
        chat_id="19",
        external_user_id="19",
        membership_role_id=4,
        user_role_id=2,
        is_owner=False,
        user=SimpleNamespace(id=19),
        workspace_role_codename="analyst",
        ai_chat_enabled=True,
    )
    active = RuntimeIdentity(**base)
    revoked = RuntimeIdentity(**{**base, "ai_chat_enabled": False})
    changed_role = RuntimeIdentity(**{**base, "workspace_role_codename": "viewer"})

    assert active.approval_binding() != revoked.approval_binding()
    assert active.approval_binding() != changed_role.approval_binding()


def test_pending_confirmation_is_rejected_after_ai_access_revocation():
    active = RuntimeIdentity(
        tenant_id=71,
        user_id=19,
        membership_id=29,
        channel="web",
        chat_id="19",
        external_user_id="19",
        membership_role_id=4,
        user_role_id=2,
        is_owner=False,
        user=SimpleNamespace(id=19),
        workspace_role_codename="analyst",
        ai_chat_enabled=True,
    )
    revoked = RuntimeIdentity(
        **{
            **active.__dict__,
            "ai_chat_enabled": False,
        }
    )
    session = SimpleNamespace(
        id=31,
        is_active=True,
        tenant_id=71,
        channel="web",
        chat_id="19",
    )
    spec = SimpleNamespace(
        name="memory_update",
        confirm=True,
        required_permission="agent.memory.update",
        parameters={"type": "object", "properties": {}},
    )
    pending = make_pending_intent(active, session, spec, {}, "call-1")

    assert pending is not None
    assert pending_rejection(pending, active, session, spec) is None
    assert pending_rejection(pending, revoked, session, spec) == "authority_changed"


def test_memory_actor_cannot_cross_workspace_or_bypass_boundary():
    with tenant_scope(71), memory_actor_scope(19, True):
        assert agent_memory._actor() == (19, True, 71)
        with tenant_scope(72):
            assert current_memory_actor() is None
            with pytest.raises(TenantContextError):
                agent_memory._actor()
        with tenant_scope(71, bypass=True):
            assert current_memory_actor() is None
            with pytest.raises(TenantContextError):
                agent_memory._actor()
        assert agent_memory._actor() == (19, True, 71)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_runtime_admits_member_only_with_ai_grant(monkeypatch, enabled):
    from unittest.mock import AsyncMock

    import app.agent.runtime as runtime

    identity = RuntimeIdentity(
        tenant_id=71,
        user_id=19,
        membership_id=29,
        channel="web",
        chat_id="19",
        external_user_id="19",
        membership_role_id=4,
        user_role_id=2,
        is_owner=False,
        user=SimpleNamespace(id=19, is_active=True),
        workspace_role_codename="viewer",
        ai_chat_enabled=enabled,
        member_grants=frozenset(),
    )
    monkeypatch.setattr(runtime, "resolve_runtime_identity", AsyncMock(return_value=identity))
    turn = AsyncMock(return_value="accepted")
    monkeypatch.setattr(runtime, "_handle_authorized_turn", turn)
    inbound = SimpleNamespace(channel="web", chat_id="19", user_id="19")
    with tenant_scope(71):
        if enabled:
            assert await runtime._handle_in_tenant(inbound, inbound) == "accepted"
            turn.assert_awaited_once()
        else:
            with pytest.raises(PermissionError):
                await runtime._handle_in_tenant(inbound, inbound)
            turn.assert_not_awaited()
