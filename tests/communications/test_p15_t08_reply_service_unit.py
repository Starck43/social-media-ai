"""Mock-only reply service tests; no PostgreSQL or provider I/O."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from app.models.communications.outbox import OutboxMessage, OutboxReplyEvidence, OutboxTarget
from app.services.communications import outbox_store, reply


def source_rows():
    thread = NS(id=11, tenant_id=7, contact_id=2, connection_id=3, channel="email")
    inbound = NS(
        id=12,
        tenant_id=7,
        thread_id=11,
        contact_id=2,
        connection_id=3,
        channel="email",
        direction="inbound",
        is_hidden=False,
        unavailable_at=None,
        sender_encrypted="Person <person@example.invalid>",
    )
    identity = NS(
        id=13,
        tenant_id=7,
        contact_id=2,
        kind="email",
        verified=True,
        connection_id=3,
        value_encrypted="person@example.invalid",
    )
    return thread, inbound, identity


class FakeSession:
    def __init__(self, rows, identities):
        self.rows = iter(rows)
        self.identities = identities
        self.added = []

    async def scalar(self, query):
        return next(self.rows)

    async def scalars(self, query):
        return NS(all=lambda: self.identities)

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, OutboxMessage):
            obj.id = 50
        if isinstance(obj, OutboxTarget):
            obj.id = 51

    async def flush(self):
        pass


def call(session):
    return reply.create_reply_draft(
        session,
        thread_id=11,
        inbound_message_id=12,
        subject="Re: test",
        body="Test",
        timezone="UTC",
        idempotency_key="test-reply-1",
    )


@pytest.mark.asyncio
async def test_reply_authority_checks_fresh_membership_and_contact_grants(monkeypatch):
    check = AsyncMock()
    monkeypatch.setattr(outbox_store, "actor_authority", check)
    await reply.reply_authority(FakeSession([NS(role_id=5), "owner"], []), 7, 3)
    check.assert_awaited_once()
    with pytest.raises(PermissionError, match="reply_denied"):
        await reply.reply_authority(FakeSession([None], []), 7, 3)
    with pytest.raises(PermissionError, match="reply_denied"):
        await reply.reply_authority(FakeSession([NS(role_id=5), "viewer"], []), 7, 3)


@pytest.mark.asyncio
async def test_reply_draft_stages_verified_source_and_evidence_in_encrypted_model(monkeypatch):
    thread, inbound, identity = source_rows()
    session = FakeSession([thread, inbound, None], [identity])
    monkeypatch.setattr(outbox_store, "current_actor", AsyncMock(return_value=(7, 3)))
    monkeypatch.setattr(reply, "reply_authority", AsyncMock())
    monkeypatch.setattr(
        outbox_store,
        "resolve_route",
        AsyncMock(
            return_value=(
                "person@example.invalid",
                "credential",
                NS(),
                NS(),
            )
        ),
    )
    headers = reply.ReplyHeaders("<message@example.invalid>", ("<message@example.invalid>",))
    monkeypatch.setattr(reply, "_read_origin_headers", lambda connection, secret, source: headers)
    result = await call(session)
    message = next(x for x in session.added if isinstance(x, OutboxMessage))
    target = next(x for x in session.added if isinstance(x, OutboxTarget))
    evidence = next(x for x in session.added if isinstance(x, OutboxReplyEvidence))
    assert result["id"] == message.id == target.message_id == evidence.outbox_message_id == 50
    assert message.reply_evidence_required is True and message.purpose == "transactional"
    assert (evidence.tenant_id, evidence.thread_id, evidence.inbound_message_id) == (7, 11, 12)
    assert '"in_reply_to":"<message@example.invalid>"' in evidence.headers_encrypted
    assert target.connection_id == 3 and target.identity_id == 13
    assert message.content_hash == outbox_store.content_digest(
        message, [target], {"thread_id": 11, "inbound_message_id": 12, "headers": headers.approval_value()}
    )


@pytest.mark.asyncio
async def test_revoked_authority_stops_before_source_reads_or_writes(monkeypatch):
    session = FakeSession([], [])
    monkeypatch.setattr(outbox_store, "current_actor", AsyncMock(return_value=(7, 3)))
    monkeypatch.setattr(reply, "reply_authority", AsyncMock(side_effect=PermissionError("reply_denied")))
    with pytest.raises(PermissionError, match="reply_denied"):
        await call(session)
    assert session.added == []


@pytest.mark.asyncio
async def test_foreign_inbound_fails_without_reading_provider_or_writing(monkeypatch):
    thread, inbound, identity = source_rows()
    inbound.tenant_id = 8
    session = FakeSession([thread, inbound], [identity])
    monkeypatch.setattr(outbox_store, "current_actor", AsyncMock(return_value=(7, 3)))
    monkeypatch.setattr(reply, "reply_authority", AsyncMock())
    route = AsyncMock()
    monkeypatch.setattr(outbox_store, "resolve_route", route)
    with pytest.raises(reply.ReplyUnavailable):
        await call(session)
    route.assert_not_awaited()
    assert session.added == []


@pytest.mark.asyncio
async def test_same_key_replays_only_matching_persisted_reply(monkeypatch):
    from app.services.communications.ingest import hash_message_id

    thread, inbound, identity = source_rows()
    inbound.external_message_id_hash = hash_message_id("<id@example.invalid>")
    monkeypatch.setattr(outbox_store, "current_actor", AsyncMock(return_value=(7, 3)))
    monkeypatch.setattr(reply, "reply_authority", AsyncMock())
    monkeypatch.setattr(
        outbox_store, "resolve_route", AsyncMock(return_value=("person@example.invalid", "credential", NS(), NS()))
    )
    monkeypatch.setattr(
        reply,
        "_read_origin_headers",
        lambda *args: reply.ReplyHeaders("<id@example.invalid>", ("<id@example.invalid>",)),
    )
    first = FakeSession([thread, inbound, None], [identity])
    await call(first)
    message = next(x for x in first.added if isinstance(x, OutboxMessage))
    evidence = next(x for x in first.added if isinstance(x, OutboxReplyEvidence))
    target = next(x for x in first.added if isinstance(x, OutboxTarget))
    monkeypatch.setattr(outbox_store, "targets_for", AsyncMock(return_value=[target]))
    monkeypatch.setattr(
        reply, "_read_origin_headers", lambda *args: pytest.fail("replay must not fetch provider headers")
    )
    replay = FakeSession([thread, inbound, message, evidence], [identity])
    assert (await call(replay))["id"] == message.id
    assert not replay.added
    evidence.headers_encrypted = evidence.headers_encrypted.replace("<id@example.invalid>", "<other@example.invalid>")
    with pytest.raises(reply.ReplyUnavailable, match="reply_evidence_unavailable"):
        await call(FakeSession([thread, inbound, message, evidence], [identity]))
    message.reply_evidence_required = False
    with pytest.raises(outbox_store.OutboxConflict, match="outbox_idempotency_mismatch"):
        await call(FakeSession([thread, inbound, message], [identity]))


@pytest.mark.asyncio
async def test_load_verified_reply_rechecks_authority_and_exact_evidence(monkeypatch):
    import json

    from app.services.communications.ingest import hash_message_id

    thread, inbound, identity = source_rows()
    inbound.external_message_id_hash = hash_message_id("<id@example.invalid>")
    message = NS(id=50, tenant_id=7, author_id=3, contact_id=2, purpose="transactional", reply_evidence_required=True)
    target = NS(tenant_id=7, message_id=50, channel="email", connection_id=3, identity_id=13)
    evidence = NS(
        tenant_id=7,
        outbox_message_id=50,
        thread_id=11,
        inbound_message_id=12,
        headers_encrypted=json.dumps(
            {"version": 1, "in_reply_to": "<id@example.invalid>", "references": ["<id@example.invalid>"]}
        ),
    )
    check = AsyncMock()
    monkeypatch.setattr(reply, "reply_authority", check)
    session = FakeSession([evidence, thread, inbound], [identity])
    proof = await reply.load_verified_reply(session, message, [target])
    check.assert_awaited_once_with(session, 7, 3)
    assert proof == {
        "thread_id": 11,
        "inbound_message_id": 12,
        "headers": {"in_reply_to": "<id@example.invalid>", "references": ["<id@example.invalid>"]},
    }
    with pytest.raises(reply.ReplyUnavailable):
        await reply.load_verified_reply(FakeSession([], []), message, [target, target])
    with pytest.raises(reply.ReplyUnavailable):
        await reply.load_verified_reply(FakeSession([None], []), message, [target])
    check.side_effect = PermissionError("reply_denied")
    with pytest.raises(PermissionError):
        await reply.load_verified_reply(FakeSession([], []), message, [target])
