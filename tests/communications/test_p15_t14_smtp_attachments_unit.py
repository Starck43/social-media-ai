"""SMTP wire preparation only: no network, credentials or provider send."""

import hashlib
from email import message_from_bytes

import pytest

from app.channels.senders.contracts import EmailAttachment, EmailRequest, MailAccount, SendBinding
from app.integrations.email.smtp_send import _prepare
from app.services.communications.attachments import AttachmentRef


def account():
    return MailAccount(SendBinding(1, 2, 3, 3, active=True, verified=True), "yandex_mail", "sender@example.com", "x")


def test_smtp_emits_real_verified_mime_leaves_and_preserves_text():
    data = b"%PDF-1.4 synthetic"
    ref = AttachmentRef(sha256=hashlib.sha256(data).hexdigest(), size=len(data), mime="application/pdf")
    request = EmailRequest("receiver@example.com", "subject", "body", attachments=(EmailAttachment(ref, data),))
    wire, _ = _prepare(account(), request)
    message = message_from_bytes(wire)
    parts = [p for p in message.walk() if p.get_filename()]
    assert len(parts) == 1
    assert parts[0].get_content_type() == "application/pdf"
    assert parts[0].get_payload(decode=True) == data
    assert message.get_payload(0).get_payload(decode=True).decode().strip() == "body"


def test_smtp_refuses_mismatched_bytes_before_building_wire():
    ref = AttachmentRef(sha256=hashlib.sha256(b"sealed").hexdigest(), size=6, mime="application/pdf")
    request = EmailRequest("receiver@example.com", "subject", "body", attachments=(EmailAttachment(ref, b"alterd"),))
    with pytest.raises(ValueError, match="invalid_attachment"):
        _prepare(account(), request)


def test_no_attachments_keeps_plain_text_wire():
    wire, _ = _prepare(account(), EmailRequest("receiver@example.com", "subject", "body"))
    assert message_from_bytes(wire).get_content_type() == "text/plain"
