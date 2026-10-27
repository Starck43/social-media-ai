"""Real-storage admission/receipt fencing; all Telegram HTTP is mocked."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.channels.telegram import TelegramChannel
from app.core.config import settings
from app.core.permissions import operator_permission_scope, permission_scope
from app.core.tenant_context import tenant_scope
from app.models import AgentAction, Tenant, TenantChannel, TenantUser, User
from app.services.actions import telegram_message as capability
from app.types import BotActionStatus
from tests.shared.fixtures.personal_workspace import personal_world

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


@pytest.fixture
async def world(personal_world, monkeypatch):
    w = personal_world
    with tenant_scope(bypass=True), operator_permission_scope():
        w.binding = await TenantChannel.objects.create(
            tenant_id=w.tenants[0].id,
            channel="telegram",
            chat_id=f"-100{10000000 + w.tenants[0].id}",
            kind="channel",
            is_active=True,
        )
    monkeypatch.setenv("P9_TELEGRAM_MESSAGE_ENABLED", "1")
    monkeypatch.setenv("P9_TELEGRAM_MESSAGE_BINDINGS", f"{w.tenants[0].id}:{w.binding.id}")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123456:synthetic-never-network")
    w.send = AsyncMock(return_value={"outcome": "sent", "message_id": "71"})
    monkeypatch.setattr(TelegramChannel, "send_part", w.send)
    with tenant_scope(w.tenants[0].id), permission_scope(w.users[0]):
        yield w


async def proposal(w, key="p9-t01-message", text="reviewed plain text"):
    return (
        await AgentAction.objects.propose_telegram_message(
            binding_id=w.binding.id,
            text=text,
            idempotency_key=key,
        )
    )[0]


async def approved(w, **kwargs):
    row = await proposal(w, **kwargs)
    return await AgentAction.objects.approve_action(row.id, expected_payload_hash=row.payload_hash)


async def execute(row):
    return await AgentAction.objects.execute_telegram_message(row.id, expected_payload_hash=row.payload_hash)


async def test_real_proposal_approval_send_and_replay_have_one_durable_receipt(world):
    w = world
    row = await proposal(w)
    assert row.dry_run is False and row.capability == capability.CAPABILITY
    replay = await proposal(w)
    assert replay.id == row.id
    with pytest.raises(ValueError, match="different intent"):
        await proposal(w, text="changed")
    assert await execute(row) is None and w.send.await_count == 0
    await AgentAction.objects.approve_action(row.id, expected_payload_hash=row.payload_hash)
    assert w.send.await_count == 0
    assert await AgentAction.objects.claim_action(row.id) is None  # generic API stays simulation-only
    result = await execute(row)
    assert result == {
        "success": True,
        "dry_run": False,
        "capability": capability.CAPABILITY,
        "outcome": "sent",
        "message_id": "71",
    }
    w.send.assert_awaited_once_with(w.binding.chat_id, "reviewed plain text", parse_mode=None)
    current = await AgentAction.objects.get(id=row.id)
    assert current.status == BotActionStatus.EXECUTED and current.attempts == 1
    assert current.claim_token_hash is None and current.result == result
    assert [h["to"] for h in current.history] == ["APPROVED", "CLAIMED", "EXECUTED"]
    assert all(h["actor_user_id"] == w.users[0].id for h in current.history)
    assert await execute(row) is None
    assert (await proposal(w)).id == row.id and w.send.await_count == 1


async def test_concurrent_requests_have_one_committed_pre_http_reservation(world):
    row = await approved(world)

    async def transport(*args, **kwargs):
        stored = await AgentAction.objects.get(id=row.id)  # independent real DB transaction
        assert stored.status == BotActionStatus.CLAIMED and stored.attempts == 1
        assert stored.error == "external_send_in_flight" and stored.claim_token_hash
        return {"outcome": "sent", "message_id": "72"}

    world.send.side_effect = transport
    results = await asyncio.gather(execute(row), execute(row))
    assert sum(r is not None for r in results) == 1 and world.send.await_count == 1


@pytest.mark.parametrize(
    "blocker",
    [
        "no_actor",
        "other_owner",
        "foreign_workspace",
        "bypass",
        "membership",
        "user",
        "tenant",
        "role",
    ],
)
async def test_fresh_actor_and_workspace_authority_never_falls_back(world, blocker):
    w = world
    row = await approved(w)
    actor, tenant, bypass = w.users[0], w.tenants[0].id, False
    if blocker == "no_actor":
        actor = None
    elif blocker == "other_owner":
        actor = w.users[1]
    elif blocker == "foreign_workspace":
        actor, tenant = w.users[2], w.tenants[1].id
    elif blocker == "bypass":
        bypass = True
    elif blocker == "membership":
        await TenantUser.objects.update_by_id(w.memberships[0].id, is_active=False)
    elif blocker == "user":
        await User.objects.update_by_id(w.users[0].id, is_active=False)
    elif blocker == "tenant":
        await Tenant.objects.update_by_id(w.tenants[0].id, is_active=False)
    elif blocker == "role":
        await TenantUser.objects.update_by_id(w.memberships[0].id, role_id=w.roles["manager"].id)
    with tenant_scope(tenant, bypass=bypass), permission_scope(actor):
        try:
            result = await execute(row)
        except PermissionError:
            result = None
    assert result is None and world.send.await_count == 0


@pytest.mark.parametrize(
    "blocker",
    [
        "inactive",
        "private",
        "group",
        "max",
        "destination",
        "deleted",
        "token",
        "off",
        "allowlist",
        "malformed_allowlist",
    ],
)
async def test_fresh_target_transport_and_kill_switch_are_mandatory(world, monkeypatch, blocker):
    row = await approved(world)
    changes = {
        "inactive": {"is_active": False},
        "private": {"kind": "private"},
        "group": {"kind": "group"},
        "max": {"channel": "max"},
        "destination": {"chat_id": "-10099999999"},
    }
    if blocker in changes:
        await TenantChannel.objects.update_by_id(world.binding.id, **changes[blocker])
    elif blocker == "deleted":
        await TenantChannel.objects.delete_by_id(world.binding.id)
    elif blocker == "token":
        monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "654321:different-synthetic")
    elif blocker == "off":
        monkeypatch.setenv("P9_TELEGRAM_MESSAGE_ENABLED", "0")
    elif blocker == "allowlist":
        monkeypatch.setenv("P9_TELEGRAM_MESSAGE_BINDINGS", "1:1")
    else:
        monkeypatch.setenv("P9_TELEGRAM_MESSAGE_BINDINGS", f"{world.tenants[0].id}:{world.binding.id},bad")
    try:
        result = await execute(row)
    except PermissionError:
        result = None
    assert result is None and world.send.await_count == 0
    assert (await AgentAction.objects.get(id=row.id)).status == BotActionStatus.APPROVED


@pytest.mark.parametrize("blocker", ["tamper", "expired", "stale_hash", "legacy_approval"])
async def test_only_the_exact_reviewed_unexpired_intent_can_send(world, blocker):
    row = await approved(world)
    if blocker == "tamper":
        await AgentAction.objects.update_by_id(row.id, payload={**row.payload, "text": "changed"})
    elif blocker == "expired":
        await AgentAction.objects.update_by_id(
            row.id, approval_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
        )
    elif blocker == "legacy_approval":
        await AgentAction.objects.update_by_id(row.id, approved_user_id=None, approved_payload_hash=None)
    else:
        row.payload_hash = "stale"
    assert await execute(row) is None and world.send.await_count == 0


@pytest.mark.parametrize(
    "response,status",
    [
        ({"outcome": "sent", "message_id": "73", "secret": "do-not-persist"}, "EXECUTED"),
        ({"outcome": "rejected", "error": "do-not-persist"}, "FAILED"),
        ({"outcome": "blocked", "error": "do-not-persist"}, "FAILED"),
        ({"outcome": "uncertain", "error": "do-not-persist"}, "UNKNOWN"),
        ({"outcome": "sent"}, "UNKNOWN"),
        ({"outcome": {}}, "UNKNOWN"),
        ([], "UNKNOWN"),
    ],
)
async def test_conservative_outcomes_are_safe_terminal_and_never_replayed(world, response, status):
    row = await approved(world)
    world.send.return_value = response
    result = await execute(row)
    current = await AgentAction.objects.get(id=row.id)
    assert current.status.name == status and "do-not-persist" not in repr(current.result)
    assert current.result == result and current.claim_token_hash is None
    assert await execute(row) is None and world.send.await_count == 1


async def test_provider_exception_is_unknown_without_leaking_error_or_retry(world):
    row = await approved(world)
    world.send.side_effect = RuntimeError("synthetic-token-do-not-persist")
    assert (await execute(row))["outcome"] == "uncertain"
    current = await AgentAction.objects.get(id=row.id)
    assert current.status == BotActionStatus.UNKNOWN and "synthetic-token" not in repr(current.result)
    assert await execute(row) is None and world.send.await_count == 1


async def test_cancellation_preserves_fence_and_expiry_reconciliation_never_resends(world):
    row = await approved(world)
    world.send.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await execute(row)
    assert (await AgentAction.objects.get(id=row.id)).status == BotActionStatus.CLAIMED
    assert await execute(row) is None and world.send.await_count == 1
    assert await AgentAction.objects.reconcile_telegram_message(row.id) is None
    await AgentAction.objects.update_by_id(row.id, claim_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    # A different active workspace owner can reconcile even if the original actor is revoked.
    await TenantUser.objects.update_by_id(world.memberships[0].id, is_active=False)
    with permission_scope(world.users[1]):
        current = await AgentAction.objects.reconcile_telegram_message(row.id)
    assert current.status == BotActionStatus.UNKNOWN and current.claim_token_hash is None
    assert current.history[-1]["actor_user_id"] == world.users[1].id and world.send.await_count == 1


async def test_revoke_between_committed_reservation_and_http_fails_closed(world, monkeypatch):
    row = await approved(world)
    original = capability._send

    async def revoked(manager, reservation):
        await TenantUser.objects.update_by_id(world.memberships[0].id, is_active=False)
        return await original(manager, reservation)

    monkeypatch.setattr(capability, "_send", revoked)
    assert (await execute(row))["outcome"] == "blocked" and world.send.await_count == 0
    assert (await AgentAction.objects.get(id=row.id)).status == BotActionStatus.FAILED


async def test_factual_receipt_survives_revocation_after_http(world, monkeypatch):
    row = await approved(world)
    original = capability._finish

    async def revoked(manager, reservation, response):
        await TenantUser.objects.update_by_id(world.memberships[0].id, is_active=False)
        return await original(manager, reservation, response)

    monkeypatch.setattr(capability, "_finish", revoked)
    assert (await execute(row))["outcome"] == "sent" and world.send.await_count == 1
    assert (await AgentAction.objects.get(id=row.id)).status == BotActionStatus.EXECUTED


async def test_uncertain_reservation_commit_never_dispatches_or_replays(world, monkeypatch):
    row = await approved(world)
    manager = AgentAction.objects
    original = manager._get_owned_session

    @asynccontextmanager
    async def committed_then_lost():
        async with original() as session:
            yield session
        raise RuntimeError("simulated lost commit acknowledgement")

    with monkeypatch.context() as patch:
        patch.setattr(manager, "_get_owned_session", committed_then_lost)
        with pytest.raises(capability.OutcomePersistenceError):
            await execute(row)
    assert (await AgentAction.objects.get(id=row.id)).status == BotActionStatus.CLAIMED
    assert await execute(row) is None and world.send.await_count == 0


async def test_receipt_storage_failure_leaves_fence_and_does_not_repeat_http(world, monkeypatch):
    row = await approved(world)
    monkeypatch.setattr(capability, "_finish", AsyncMock(side_effect=RuntimeError("storage failure")))
    with pytest.raises(capability.OutcomePersistenceError):
        await execute(row)
    assert (await AgentAction.objects.get(id=row.id)).status == BotActionStatus.CLAIMED
    assert await execute(row) is None and world.send.await_count == 1


async def test_target_revocation_serializes_with_admitted_http(world):
    row = await approved(world)
    entered, release = asyncio.Event(), asyncio.Event()

    async def transport(*args, **kwargs):
        entered.set()
        await release.wait()
        return {"outcome": "sent", "message_id": "74"}

    world.send.side_effect = transport
    send = asyncio.create_task(execute(row))
    await asyncio.wait_for(entered.wait(), 5)
    revoke = asyncio.create_task(TenantChannel.objects.update_by_id(world.binding.id, is_active=False))
    try:
        await asyncio.sleep(0.05)
        assert not revoke.done()  # the final authorization holds the target lock during HTTP
    finally:
        release.set()
    result, _ = await asyncio.gather(send, revoke)
    assert result["outcome"] == "sent" and world.send.await_count == 1


async def test_generic_preview_and_forged_mode_are_not_an_activation_path(world):
    row, _ = await AgentAction.objects.propose(
        idempotency_key="generic",
        capability=capability.CAPABILITY,
        target_ref=f"tenant-channel:{world.binding.id}",
        payload={"text": "legacy"},
    )
    await AgentAction.objects.approve_action(row.id)
    assert await execute(row) is None
    await AgentAction.objects.update_by_id(row.id, dry_run=False)
    assert await execute(row) is None and world.send.await_count == 0


async def test_factual_receipt_preserves_captured_actor_after_membership_deletion(world, monkeypatch):
    row = await approved(world)
    original = capability._finish

    async def deleted(manager, reservation, response):
        await TenantUser.objects.delete_by_id(world.memberships[0].id)
        return await original(manager, reservation, response)

    monkeypatch.setattr(capability, "_finish", deleted)
    assert (await execute(row))["outcome"] == "sent"
    current = await AgentAction.objects.get(id=row.id)
    assert current.status == BotActionStatus.EXECUTED and current.confirmed_by is None
    assert current.history[-1]["actor_membership_id"] == world.memberships[0].id


async def test_approval_revocation_after_reservation_blocks_final_http_admission(world, monkeypatch):
    row = await approved(world)
    original = capability._send

    async def revoked(manager, reservation):
        await AgentAction.objects.update_by_id(row.id, approved_payload_hash=None)
        return await original(manager, reservation)

    monkeypatch.setattr(capability, "_send", revoked)
    assert (await execute(row))["outcome"] == "blocked" and world.send.await_count == 0


async def test_master_kill_switch_is_rechecked_after_committed_reservation(world, monkeypatch):
    row = await approved(world)
    original = capability._send

    async def disabled(manager, reservation):
        monkeypatch.setenv("P9_TELEGRAM_MESSAGE_ENABLED", "0")
        return await original(manager, reservation)

    monkeypatch.setattr(capability, "_send", disabled)
    assert (await execute(row))["outcome"] == "blocked" and world.send.await_count == 0
    current = await AgentAction.objects.get(id=row.id)
    assert current.status == BotActionStatus.FAILED and current.attempts == 1


@pytest.mark.parametrize("endpoint", ["http://api.telegram.org", "https://unapproved.invalid", "http://localhost:8081"])
async def test_capability_never_sends_credentials_to_an_alternate_endpoint(world, monkeypatch, endpoint):
    import app.channels.telegram as transport

    row = await approved(world)
    monkeypatch.setattr(transport, "API_BASE", endpoint)
    with pytest.raises(PermissionError, match="official HTTPS"):
        await execute(row)
    assert await AgentAction.objects.telegram_message_targets() == []
    assert world.send.await_count == 0
