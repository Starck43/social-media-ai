"""A reply marker alone never grants outbox authority (D15); only verified evidence does."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.communications import outbox_store as module
from app.services.communications.outbox_contracts import ScheduleIn


@pytest.mark.asyncio
async def test_required_evidence_cannot_schedule_when_evidence_is_unavailable(monkeypatch):
    monkeypatch.setattr(module, "current_actor", AsyncMock(return_value=(1, 1)))
    monkeypatch.setattr(module, "owned_message", AsyncMock(return_value=SimpleNamespace(reply_evidence_required=True)))
    monkeypatch.setattr(module, "targets_for", AsyncMock(return_value=[SimpleNamespace()]))
    monkeypatch.setattr(
        module, "verified_reply_for_outbox", AsyncMock(side_effect=module.OutboxConflict("reply_evidence_unavailable"))
    )
    request = ScheduleIn(
        message_id=1,
        expected_content_hash="sha256:" + "a" * 64,
        scheduled_at=datetime.now(timezone.utc),
    )
    with pytest.raises(module.OutboxConflict, match="^reply_evidence_unavailable$"):
        await module.outbox_store.schedule(request, session=object())


@pytest.mark.parametrize("claimed", [False, True])
def test_required_evidence_cannot_claim_or_deliver_without_verified_evidence(claimed):
    message = SimpleNamespace(reply_evidence_required=True)
    with pytest.raises(module.OutboxConflict, match="^reply_evidence_unavailable$"):
        module.verify_approval(message, [], None, None, [], datetime.now(timezone.utc), claimed=claimed)


def test_marker_alone_never_makes_an_item_sendable():
    """The flag is a requirement, not a credential: without evidence, dispatch is refused."""
    message = SimpleNamespace(
        author_id=3,
        contact_id=2,
        subject_encrypted="Re: test",
        content_encrypted="hello",
        purpose="transactional",
        timezone="UTC",
        reply_evidence_required=True,
    )
    with pytest.raises(module.OutboxConflict, match="reply_evidence_unavailable"):
        module.content_digest(message, [], None)


def test_reply_content_digest_binds_evidence_and_missing_evidence_fails():
    message = SimpleNamespace(
        author_id=3,
        contact_id=2,
        subject_encrypted="Re: test",
        content_encrypted="hello",
        purpose="transactional",
        timezone="UTC",
        reply_evidence_required=True,
    )
    target = SimpleNamespace(
        channel="email",
        connection_id=4,
        identity_id=5,
        recipient_encrypted="person@example.invalid",
        credential_hash="credential",
    )
    evidence = {
        "thread_id": 11,
        "inbound_message_id": 12,
        "headers": {"in_reply_to": "<id@example.invalid>", "references": ["<id@example.invalid>"]},
    }
    baseline = module.content_digest(message, [target], evidence)
    changed = {
        **evidence,
        "headers": {**evidence["headers"], "references": ["<other@example.invalid>", "<id@example.invalid>"]},
    }
    assert module.content_digest(message, [target], changed) != baseline
    assert module.content_digest(message, [target], {**evidence, "thread_id": 99}) != baseline
    assert module.content_digest(message, [target], {**evidence, "inbound_message_id": 99}) != baseline
    with pytest.raises(module.OutboxConflict, match="reply_evidence_unavailable"):
        module.content_digest(message, [target])
    message.reply_evidence_required = False
    assert module.content_digest(message, [target]) != baseline
    with pytest.raises(module.OutboxConflict, match="reply_evidence_unexpected"):
        module.content_digest(message, [target], evidence)
