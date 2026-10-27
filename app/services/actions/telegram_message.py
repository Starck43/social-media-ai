"""One manually approved Telegram channel message; no generic live executor.

The committed reservation is the send fence. Only its winning caller gets the
in-memory dispatch object; requests cannot supply/replay it. Telegram offers no
sendMessage idempotency key: uncertain reservations are never sent again.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select

CAPABILITY = "telegram.bound_message.v1"


class OutcomePersistenceError(RuntimeError):
    """HTTP may have happened; inspect/reconcile, never replay this action."""


def released(tenant_id: int, binding_id: int) -> bool:
    """Deployment master switch AND exact operator-selected workspace/binding pair."""
    if os.environ.get("P9_TELEGRAM_MESSAGE_ENABLED", "0") != "1":
        return False
    entries = os.environ.get("P9_TELEGRAM_MESSAGE_BINDINGS", "").split(",")
    if not entries or any(not re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", x) for x in entries):
        return False
    return f"{tenant_id}:{binding_id}" in entries


def valid_destination(value) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"-100[1-9][0-9]{0,12}", value) is not None
        and abs(int(value)) <= 2**52 - 1
    )


def valid_text(value) -> bool:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return False
    try:
        return len(value.encode("utf-16-le")) // 2 <= 4096
    except UnicodeEncodeError:
        return False


def valid_payload(payload) -> bool:
    return (
        isinstance(payload, dict)
        and set(payload) == {"binding_id", "destination_id", "text", "transport_fingerprint"}
        and type(payload["binding_id"]) is int
        and payload["binding_id"] > 0
        and valid_destination(payload["destination_id"])
        and valid_text(payload["text"])
        and isinstance(payload["transport_fingerprint"], str)
        and re.fullmatch(r"[0-9a-f]{64}", payload["transport_fingerprint"]) is not None
    )


def _scope() -> int:
    from app.core.tenant_context import current_tenant_id, is_bypass

    tenant_id = current_tenant_id()
    if is_bypass() or type(tenant_id) is not int or tenant_id <= 0:
        raise PermissionError("Explicit non-bypass workspace required")
    return tenant_id


def _transport():
    from app.channels.telegram import API_BASE, TelegramChannel

    if API_BASE != "https://api.telegram.org":
        raise PermissionError("This capability requires the official HTTPS Telegram endpoint")
    channel = TelegramChannel()
    if not channel.enabled or not isinstance(channel.token, str):
        raise PermissionError("Telegram transport unavailable")
    identity = hashlib.sha256(f"{API_BASE}\x00{channel.token}".encode()).hexdigest()
    return channel, identity


async def _owner(manager, session):
    from app.models import WorkspaceRole

    _scope()
    actor, member = await manager._actor(session)
    role = await session.scalar(select(WorkspaceRole.codename).where(WorkspaceRole.id == member.role_id))
    if role != "owner":
        raise PermissionError("Workspace owner required for this capability")
    return actor, member


async def _target(session, binding_id):
    from app.models import TenantChannel

    if type(binding_id) is not int or binding_id <= 0:
        raise ValueError("Invalid channel binding")
    tenant_id = _scope()
    if not released(tenant_id, binding_id):
        raise PermissionError("Telegram capability not released for this binding")
    binding = await session.scalar(
        select(TenantChannel)
        .where(TenantChannel.id == binding_id, TenantChannel.tenant_id == tenant_id)
        .with_for_update()
    )
    if (
        binding is None
        or not binding.is_active
        or binding.channel != "telegram"
        or binding.kind != "channel"
        or not valid_destination(binding.chat_id)
    ):
        raise PermissionError("Active Telegram channel binding required")
    return binding


async def targets(manager):
    from app.models import TenantChannel

    try:
        _transport()
        async with manager._get_owned_session() as session:
            await _owner(manager, session)
            bindings = (
                await session.scalars(
                    select(TenantChannel).where(
                        TenantChannel.tenant_id == _scope(),
                        TenantChannel.is_active.is_(True),
                        TenantChannel.channel == "telegram",
                        TenantChannel.kind == "channel",
                    )
                )
            ).all()
            return [
                {"id": b.id, "chat_id": b.chat_id}
                for b in bindings
                if released(_scope(), b.id) and valid_destination(b.chat_id)
            ]
    except PermissionError:
        return []


async def propose(manager, *, binding_id, text, idempotency_key):
    if not valid_text(text) or not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
        raise ValueError("Invalid message or idempotency key")
    async with manager._get_owned_session() as session:
        await _owner(manager, session)
        binding = await _target(session, binding_id)
        _, transport_identity = _transport()
        values = dict(
            tenant_id=_scope(),
            idempotency_key=idempotency_key,
            capability=CAPABILITY,
            target_ref=f"tenant-channel:{binding.id}",
            origin="manual",
            dry_run=False,
            payload={
                "binding_id": binding.id,
                "destination_id": binding.chat_id,
                "text": text,
                "transport_fingerprint": transport_identity,
            },
            origin_snapshot={},
        )
        values["payload_hash"] = manager.fingerprint(values)
        return await manager._insert_proposal(session, values)


@dataclass(frozen=True)
class _Reservation:
    action_id: int
    tenant_id: int
    actor_id: int
    membership_id: int
    binding_id: int
    destination: str
    text: str = field(repr=False)
    payload_hash: str
    token: str = field(repr=False)
    channel: object = field(repr=False)


async def _reserve(manager, action_id, expected_payload_hash):
    from app.types import BotActionStatus

    if type(action_id) is not int or action_id <= 0 or not isinstance(expected_payload_hash, str):
        raise ValueError("Invalid action identity")
    # Own and COMMIT the reservation before returning anything that permits HTTP.
    # This API intentionally accepts no caller-managed/uncommitted session.
    async with manager._get_owned_session() as session:
        actor, member = await _owner(manager, session)
        row = await manager._locked(session, action_id)
        if row is None or row.status != BotActionStatus.APPROVED:
            return None
        now = datetime.now(timezone.utc)
        if (
            row.capability != CAPABILITY
            or row.dry_run is not False
            or row.origin != "manual"
            or row.attempts != 0
            or not row.idempotency_key
            or not valid_payload(row.payload)
            or any(
                getattr(row, key) is not None
                for key in ("source_id", "agent_scenario_id", "agent_task_id", "analytics_id")
            )
            or row.approved_user_id != actor.id
            or row.confirmed_by != member.id
            or row.approval_expires_at is None
            or row.approval_expires_at <= now
        ):
            return None
        fingerprint = manager.fingerprint(row)
        if not (fingerprint == expected_payload_hash == row.payload_hash == row.approved_payload_hash):
            return None
        binding = await _target(session, row.payload["binding_id"])
        channel, transport_identity = _transport()
        if (
            row.target_ref != f"tenant-channel:{binding.id}"
            or binding.chat_id != row.payload["destination_id"]
            or transport_identity != row.payload["transport_fingerprint"]
        ):
            return None
        token = secrets.token_urlsafe(32)
        manager._record(row, actor, member, BotActionStatus.CLAIMED)
        row.status = BotActionStatus.CLAIMED
        row.claim_token_hash = hashlib.sha256(token.encode()).hexdigest()
        row.claimed_at, row.claim_expires_at = now, now + timedelta(seconds=60)
        row.attempts, row.error = 1, "external_send_in_flight"
        await session.flush()
        reservation = _Reservation(
            row.id,
            _scope(),
            actor.id,
            member.id,
            binding.id,
            binding.chat_id,
            row.payload["text"],
            fingerprint,
            token,
            channel,
        )
    return reservation  # __aexit__ committed successfully; uncertainty raises, never sends


def _outcome(response):
    if not isinstance(response, dict):
        return "uncertain", None
    outcome = response.get("outcome")
    receipt = response.get("message_id")
    if outcome == "sent" and isinstance(receipt, str) and re.fullmatch(r"[1-9][0-9]{0,19}", receipt):
        return "sent", receipt
    if isinstance(outcome, str) and outcome in {"blocked", "rejected"}:
        return outcome, None
    return "uncertain", None


async def _finish(manager, reservation, response):
    from app.types import BotActionStatus

    outcome, receipt = _outcome(response)
    async with manager._get_owned_session() as session:
        if _scope() != reservation.tenant_id:
            raise PermissionError("Reservation workspace changed")
        row = await manager._locked(session, reservation.action_id)
        if (
            row is None
            or row.status != BotActionStatus.CLAIMED
            or row.capability != CAPABILITY
            or row.dry_run is not False
            or not row.claim_token_hash
            or not secrets.compare_digest(row.claim_token_hash, hashlib.sha256(reservation.token.encode()).hexdigest())
            or manager.fingerprint(row) != reservation.payload_hash
        ):
            raise OutcomePersistenceError("Outcome requires operator reconciliation")
        status = {
            "sent": BotActionStatus.EXECUTED,
            "rejected": BotActionStatus.FAILED,
            "blocked": BotActionStatus.FAILED,
            "uncertain": BotActionStatus.UNKNOWN,
        }[outcome]
        # Recording a factual receipt authorizes NO send. Keep it possible after
        # actor revocation; the dispatch actor was captured in the committed fence.
        manager._record(
            row, SimpleNamespace(id=reservation.actor_id), SimpleNamespace(id=reservation.membership_id), status
        )
        row.status, row.claim_token_hash = status, None
        row.executed_at = datetime.now(timezone.utc)
        row.error = None if outcome == "sent" else f"external_send_{outcome}"
        row.result = {"dry_run": False, "capability": CAPABILITY, "outcome": outcome, "success": outcome == "sent"}
        if receipt is not None:
            row.result = {**row.result, "message_id": receipt}
        await session.flush()
        return dict(row.result)


async def _send(manager, reservation):
    """Freshly reauthorize and hold DB authority/target locks over the one HTTP call.

    Only execute()'s winning reservation reaches this private helper. No route,
    worker retry or user-supplied token can invoke a reserved send independently.
    """
    from app.types import BotActionStatus

    async with manager._get_owned_session() as session:
        try:
            actor, member = await _owner(manager, session)
            row = await manager._locked(session, reservation.action_id)
            binding = await _target(session, reservation.binding_id)
            _, identity = _transport()
            if (
                row is None
                or row.status != BotActionStatus.CLAIMED
                or actor.id != reservation.actor_id
                or member.id != reservation.membership_id
                or row.approved_user_id != actor.id
                or row.confirmed_by != member.id
                or row.claim_token_hash != hashlib.sha256(reservation.token.encode()).hexdigest()
                or not (
                    manager.fingerprint(row)
                    == row.payload_hash
                    == row.approved_payload_hash
                    == reservation.payload_hash
                )
                or row.approval_expires_at is None
                or row.approval_expires_at <= datetime.now(timezone.utc)
                or row.claim_expires_at is None
                or row.claim_expires_at <= datetime.now(timezone.utc)
                or binding.chat_id != reservation.destination
                or identity != row.payload["transport_fingerprint"]
                or not released(reservation.tenant_id, reservation.binding_id)
            ):
                return {"outcome": "blocked"}
        except PermissionError:
            return {"outcome": "blocked"}  # proven pre-HTTP refusal
        try:
            return await reservation.channel.send_part(reservation.destination, reservation.text, parse_mode=None)
        except Exception:
            return {"outcome": "uncertain"}  # no raw provider body/exception persists


async def execute(manager, action_id, *, expected_payload_hash):
    try:
        reservation = await _reserve(manager, action_id, expected_payload_hash)
    except (PermissionError, ValueError):
        raise
    except Exception:
        raise OutcomePersistenceError("Dispatch reservation unconfirmed; inspect before retry") from None
    if reservation is None:
        return None
    try:
        response = await _send(manager, reservation)
    except Exception:
        response = {"outcome": "uncertain"}
    # Cancellation/process death leaves the committed fence intact, never replayed.
    try:
        return await _finish(manager, reservation, response)
    except Exception:
        raise OutcomePersistenceError("External outcome unconfirmed; inspect before retry") from None


async def reconcile(manager, action_id):
    """Record an expired dispatch as UNKNOWN. Never release the fence or resend."""
    from app.types import BotActionStatus

    async with manager._get_owned_session() as session:
        actor, member = await _owner(manager, session)
        row = await manager._locked(session, action_id)
        if (
            row is None
            or row.capability != CAPABILITY
            or row.dry_run is not False
            or row.status != BotActionStatus.CLAIMED
            or row.claim_expires_at is None
            or row.claim_expires_at > datetime.now(timezone.utc)
        ):
            return None
        manager._record(row, actor, member, BotActionStatus.UNKNOWN)
        row.status, row.claim_token_hash, row.error = BotActionStatus.UNKNOWN, None, "external_send_outcome_unconfirmed"
        row.result = {"dry_run": False, "capability": CAPABILITY, "outcome": "uncertain", "success": False}
        await session.flush()
        return row
