"""Attachment references are bound to the approval hash and persisted, never caller bytes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from app.services.communications.outbox_contracts import DraftIn
from app.services.communications.outbox_store import OutboxConflict, OutboxStore, digest


def draft(**extra):
    return DraftIn.model_validate(
        {
            "contact_id": 1,
            "body": "body",
            "targets": [{"channel": "email", "connection_id": 1, "identity_id": 2}],
            **extra,
        }
    )


def attachment(**extra):
    return {
        "sha256": "b" * 64,
        "size": 10,
        "mime": "application/pdf",
        **extra,
    }


@pytest.mark.asyncio
async def test_empty_attachments_replay_legacy_request_hash():
    request = draft()
    legacy = request.model_dump(mode="json")
    assert legacy.pop("attachments") == []
    existing = SimpleNamespace(request_hash=digest(legacy))
    session = SimpleNamespace(scalar=AsyncMock(return_value=existing))
    with (
        patch("app.services.communications.outbox_store.current_actor", new=AsyncMock(return_value=(1, 2))),
        patch("app.services.communications.outbox_store.targets_for", new=AsyncMock(return_value=[])),
        patch("app.services.communications.outbox_store.projection", return_value="replayed"),
    ):
        assert await OutboxStore().draft(request, "key", session=session) == "replayed"


def test_caller_bytes_are_not_accepted():
    with pytest.raises(ValidationError):
        draft(attachments=[attachment(content="caller bytes")])


def test_attachment_set_is_bound_into_the_content_digest():
    """Editing the attachment set must not approve under the previous hash."""
    from app.services.communications.outbox_store import attachment_digest

    first = attachment_digest([SimpleNamespace(sha256="a" * 64, size=10, mime="application/pdf")])
    swapped = attachment_digest([SimpleNamespace(sha256="c" * 64, size=10, mime="application/pdf")])
    resized = attachment_digest([SimpleNamespace(sha256="a" * 64, size=11, mime="application/pdf")])
    retyped = attachment_digest([SimpleNamespace(sha256="a" * 64, size=10, mime="text/plain")])

    assert first != swapped
    assert first != resized
    assert first != retyped
    assert first == attachment_digest([SimpleNamespace(sha256="a" * 64, size=10, mime="application/pdf")])
    assert first[0]["position"] == 0


def test_attachment_digest_is_absent_for_an_empty_set():
    from app.services.communications.outbox_store import attachment_digest

    assert attachment_digest([]) == []


def test_request_hash_differs_when_only_the_attachment_set_differs():
    """Same idempotency key, different set -> different hash -> OutboxConflict upstream."""
    from app.services.communications import outbox_store as mod

    def request_hash(req):
        data = req.model_dump(mode="json")
        data.pop("attachments")
        if req.attachments:
            data["attachments"] = mod.attachment_digest(
                [SimpleNamespace(sha256=a.sha256, size=a.size, mime=a.mime) for a in req.attachments]
            )
        return digest(data)

    assert request_hash(draft(attachments=[attachment()])) != request_hash(
        draft(attachments=[attachment(sha256="c" * 64)])
    )
    assert request_hash(draft(attachments=[attachment()])) != request_hash(draft())
    assert request_hash(draft(attachments=[attachment()])) == request_hash(draft(attachments=[attachment()]))
