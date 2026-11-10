"""Attachment admission is fail-closed until the reference store exists."""

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
        "evidence_id": 1,
        "revision_id": 2,
        "revision_body_hash": "sha256:" + "a" * 64,
        "attachment_bytes_hash": "sha256:" + "b" * 64,
        "size": 10,
        "media_type": "application/pdf",
        "filename": "document.pdf",
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


@pytest.mark.asyncio
async def test_attachment_ref_is_refused_before_database_access():
    with patch("app.services.communications.outbox_store.current_actor") as actor:
        with pytest.raises(OutboxConflict, match="attachment_storage_not_ready"):
            await OutboxStore().draft(draft(attachments=[attachment()]), "key", session=object())
        actor.assert_not_called()
