"""DB-free checks for staged D15 wiring; the reply guard still denies delivery."""

from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from app.services.communications import outbox_store, reply


@pytest.mark.asyncio
async def test_staged_proof_is_server_loaded_and_permission_denials_propagate(monkeypatch):
    message, targets, session = NS(reply_evidence_required=True), [object()], object()
    proof = {"thread_id": 3, "inbound_message_id": 4, "headers": {"in_reply_to": "<a@b>", "references": ["<a@b>"]}}
    load = AsyncMock(return_value=proof)
    monkeypatch.setattr(reply, "load_verified_reply", load)
    assert await outbox_store.verified_reply_for_outbox(session, message, targets) == proof
    load.assert_awaited_once_with(session, message, targets)
    load.side_effect = PermissionError("reply_denied")
    with pytest.raises(PermissionError, match="reply_denied"):
        await outbox_store.verified_reply_for_outbox(session, message, targets)
    load.side_effect = reply.ReplyUnavailable("no source")
    with pytest.raises(outbox_store.OutboxConflict, match="reply_evidence_unavailable"):
        await outbox_store.verified_reply_for_outbox(session, message, targets)
    message.reply_evidence_required = False
    load.reset_mock()
    assert await outbox_store.verified_reply_for_outbox(session, message, targets) is None
    load.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("proof,consent", [(None, True), ({"verified": "staged"}, False)])
async def test_staged_snapshot_uses_consent_without_reply_evidence(monkeypatch, proof, consent):
    target = NS(recipient_encrypted="person@example.invalid", credential_hash="snapshot")
    connection, secret = object(), object()
    resolve = AsyncMock(return_value=(target.recipient_encrypted, "snapshot", connection, secret))
    monkeypatch.setattr(outbox_store, "resolve_route", resolve)
    session, message, now = object(), object(), datetime.now(timezone.utc)
    assert await outbox_store.verify_snapshot(session, message, target, now, reply_evidence=proof) == (
        connection,
        secret,
    )
    resolve.assert_awaited_once_with(session, message, target, now, consent=consent)
    resolve.return_value = ("changed@example.invalid", "snapshot", connection, secret)
    with pytest.raises(outbox_store.OutboxConflict, match="outbox_route_changed"):
        await outbox_store.verify_snapshot(session, message, target, now, reply_evidence=proof)
