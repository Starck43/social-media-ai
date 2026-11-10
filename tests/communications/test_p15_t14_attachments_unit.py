"""Spool-shaped attachment references; no DB, network, filesystem or provider."""
import hashlib
from email import message_from_bytes
from email.message import EmailMessage

import pytest
from pydantic import ValidationError

from app.services.communications.attachments import (
    AttachmentRef,
    AttachmentRejected,
    attach_to_message,
    channel_supports_attachments,
    validate_attachment_set,
    verify_delivered_bytes,
)


def ref(data=b"pdf", **kw):
    values = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "mime": "application/pdf"}
    values.update(kw)
    return AttachmentRef(**values)


def test_exact_spool_shape_and_forbidden_caller_bytes():
    item = ref()
    assert set(item.model_dump()) == {"sha256", "size", "mime"}
    with pytest.raises(ValidationError):
        ref(evidence_id=1)
    with pytest.raises(ValidationError):
        ref(payload=b"caller bytes")
    with pytest.raises(ValidationError):
        ref(filename="../../escape")


@pytest.mark.parametrize("channel", ["telegram", "max", "unknown", ""])
def test_non_email_fails_closed(channel):
    assert not channel_supports_attachments(channel)
    with pytest.raises(AttachmentRejected, match="channel_cannot_attach"):
        validate_attachment_set([ref()], channel)


def test_order_and_duplicate():
    first, second = ref(b"a"), ref(b"b")
    assert validate_attachment_set([first, second], "email") == (first, second)
    with pytest.raises(AttachmentRejected, match="duplicate_attachment"):
        validate_attachment_set([first, first], "email")


def test_limits_are_settings_not_hardcoded(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ATTACHMENT_MAX_FILE_BYTES", 3)
    monkeypatch.setattr(settings, "ATTACHMENT_MAX_TOTAL_BYTES", 4)
    assert validate_attachment_set([ref(b"abc")], "email")
    with pytest.raises(AttachmentRejected, match="attachment_too_large"):
        validate_attachment_set([ref(b"abcd")], "email")
    with pytest.raises(AttachmentRejected, match="attachment_set_too_large"):
        validate_attachment_set([ref(b"abc"), ref(b"de")], "email")


def test_empty_and_count_rejected():
    with pytest.raises(AttachmentRejected, match="no_attachments"):
        validate_attachment_set([], "email")
    with pytest.raises(AttachmentRejected, match="too_many_attachments"):
        validate_attachment_set([ref(str(i).encode()) for i in range(11)], "email")


@pytest.mark.parametrize("digest", ["sha256:" + "a" * 64, "a" * 63, "A" * 64, "g" * 64])
def test_spool_hash_format(digest):
    with pytest.raises(ValidationError):
        ref(sha256=digest)


@pytest.mark.parametrize("changes", [{"size": 0}, {"size": True}, {"mime": "application/x-msdownload"}])
def test_invalid_metadata(changes):
    with pytest.raises(ValidationError):
        ref(**changes)


def test_hash_size_checks_and_real_mime_roundtrip():
    data = b"%PDF-1.4\x00\xff"
    item = ref(data)
    verify_delivered_bytes(item, data)
    with pytest.raises(AttachmentRejected, match="attachment_size_mismatch"):
        verify_delivered_bytes(item, data + b"x")
    with pytest.raises(AttachmentRejected, match="attachment_hash_mismatch"):
        verify_delivered_bytes(item, b"x" * len(data))
    message = EmailMessage()
    message.set_content("body")
    attach_to_message(message, item, data)
    wire = message_from_bytes(message.as_bytes())
    leaves = [part for part in wire.walk() if part.get_filename()]
    assert len(leaves) == 1 and leaves[0].get_content_type() == "application/pdf"
    assert leaves[0].get_payload(decode=True) == data
    assert leaves[0].get_filename().endswith(".pdf")
