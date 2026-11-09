"""Pure unit tests for the P15-T14 attachment contract (no DB, no network, no files)."""

import base64
import hashlib
from email import message_from_bytes
from email.message import EmailMessage

import pytest
from pydantic import ValidationError

from app.services.communications.attachments import (
    ATTACHMENT_MEDIA_TYPES,
    AttachmentRef,
    AttachmentRejected,
    attach_to_message,
    build_mime_part,
    channel_supports_attachments,
    validate_attachment_set,
    verify_delivered_bytes,
)

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _ref(data: bytes = b"%PDF-1.4 report", **overrides) -> AttachmentRef:
    fields = {
        "evidence_id": 1,
        "revision_id": 10,
        "revision_body_hash": _hash(b"document body"),
        "attachment_bytes_hash": _hash(data),
        "size": len(data),
        "media_type": PDF,
        "filename": "report.pdf",
    }
    fields.update(overrides)
    return AttachmentRef(**fields)


def test_happy_path_preserves_order() -> None:
    first, second = _ref(), _ref(data=b"%PDF other", revision_id=11, filename="other.pdf")
    result = validate_attachment_set([first, second], "email")
    assert isinstance(result, tuple)
    assert result == (first, second)


def test_channel_support_fails_closed() -> None:
    assert channel_supports_attachments("email") is True
    for channel in ("telegram", "max", "", "unknown"):
        assert channel_supports_attachments(channel) is False


def test_channel_cannot_attach_refused_at_scheduling() -> None:
    with pytest.raises(AttachmentRejected) as exc:
        validate_attachment_set([_ref()], "telegram")
    assert exc.value.code == "channel_cannot_attach"


def test_no_attachments() -> None:
    with pytest.raises(AttachmentRejected) as exc:
        validate_attachment_set([], "email")
    assert exc.value.code == "no_attachments"


def test_too_many_attachments() -> None:
    refs = [_ref(data=b"x", revision_id=i + 1, attachment_bytes_hash=_hash(str(i).encode())) for i in range(11)]
    with pytest.raises(AttachmentRejected) as exc:
        validate_attachment_set(refs, "email")
    assert exc.value.code == "too_many_attachments"


def test_total_size_exceeded() -> None:
    big = 12 * 1024 * 1024
    refs = [
        _ref(size=big, revision_id=1, attachment_bytes_hash=_hash(b"a")),
        _ref(size=big, revision_id=2, attachment_bytes_hash=_hash(b"b")),
        _ref(size=big, revision_id=3, attachment_bytes_hash=_hash(b"c")),
    ]
    with pytest.raises(AttachmentRejected) as exc:
        validate_attachment_set(refs, "email")
    assert exc.value.code == "attachment_size_exceeded"


def test_duplicate_attachment() -> None:
    ref = _ref()
    with pytest.raises(AttachmentRejected) as exc:
        validate_attachment_set([ref, ref], "email")
    assert exc.value.code == "duplicate_attachment"


def test_revision_render_mismatch() -> None:
    a = _ref(data=b"one", attachment_bytes_hash=_hash(b"one"))
    b = _ref(data=b"two", attachment_bytes_hash=_hash(b"two"))
    with pytest.raises(AttachmentRejected) as exc:
        validate_attachment_set([a, b], "email")
    assert exc.value.code == "revision_render_mismatch"


def test_verify_delivered_bytes_pass_and_fail() -> None:
    data = b"%PDF-1.4 exact"
    ref = _ref(data=data)
    verify_delivered_bytes(ref, data)
    with pytest.raises(AttachmentRejected) as exc:
        verify_delivered_bytes(ref, b"%PDF-1.4 EXACT")
    assert exc.value.code == "attachment_hash_mismatch"
    with pytest.raises(AttachmentRejected) as exc:
        verify_delivered_bytes(ref, data + b"extra")
    assert exc.value.code == "attachment_size_mismatch"


def test_mime_part_round_trips_bytes() -> None:
    data = b"%PDF-1.4 \x00\x01 binary-ish payload \xff"
    ref = _ref(data=data, filename="quarterly report.pdf")
    part = build_mime_part(ref, data)
    assert part["Content-Type"] == PDF
    assert part["Content-Transfer-Encoding"] == "base64"
    assert part["Content-Disposition"].startswith("attachment")
    assert "quarterly report.pdf" in part["Content-Disposition"]
    assert base64.b64decode(part.get_payload()) == data


def test_mime_part_refuses_mismatched_bytes() -> None:
    ref = _ref(data=b"sealed")
    with pytest.raises(AttachmentRejected) as exc:
        build_mime_part(ref, b"other")
    assert exc.value.code in {"attachment_hash_mismatch", "attachment_size_mismatch"}


def test_attached_parts_are_files_not_forwarded_messages() -> None:
    """Regression: a leaf part handed straight to ``add_attachment`` becomes ``message/rfc822``.

    ``EmailMessage`` starts as ``text/plain`` and wraps any non-``multipart`` child, so the
    provider would receive an attached email with no file. ``attach_to_message`` must switch
    the parent to ``multipart/mixed`` and keep every attachment a real leaf of its own type.
    """
    pdf, docx = b"%PDF-1.4 first", b"PK\x03\x04 second"
    message = EmailMessage()
    message["Subject"] = "report"
    message.set_content("Please find the documents attached.")
    attach_to_message(message, _ref(data=pdf, revision_id=1, filename="report.pdf"), pdf)
    attach_to_message(message, _ref(data=docx, revision_id=2, media_type=DOCX, filename="report.docx"), docx)

    assert message.get_content_type() == "multipart/mixed"
    attachments = list(message.iter_attachments())
    assert [(part.get_content_type(), part.get_filename()) for part in attachments] == [
        (PDF, "report.pdf"),
        (DOCX, "report.docx"),
    ]
    assert all(part.get_content_maintype() != "message" for part in attachments)
    assert [part.get_content() for part in attachments] == [pdf, docx]

    wire = message_from_bytes(message.as_bytes())
    parsed = [(part.get_content_type(), part.get_filename()) for part in wire.walk() if part.get_filename()]
    assert parsed == [(PDF, "report.pdf"), (DOCX, "report.docx")]
    assert wire.get_payload(0).get_payload(decode=True).decode().strip() == "Please find the documents attached."


def test_attach_to_message_refuses_mismatched_bytes() -> None:
    message = EmailMessage()
    message.set_content("body")
    with pytest.raises(AttachmentRejected):
        attach_to_message(message, _ref(data=b"sealed"), b"tampered")
    assert message.get_content_type() == "text/plain"


@pytest.mark.parametrize("filename", ["../evil.pdf", "dir/evil.pdf", "dir\\evil.pdf", ".hidden", "a\r\nb.pdf", ""])
def test_filename_rejects_unsafe_values(filename: str) -> None:
    with pytest.raises(ValidationError):
        _ref(filename=filename)


def test_filename_length_bound() -> None:
    _ref(filename="a" * 200)
    with pytest.raises(ValidationError):
        _ref(filename="a" * 201)


def test_media_type_allowlist() -> None:
    assert DOCX in ATTACHMENT_MEDIA_TYPES
    _ref(media_type=DOCX)
    with pytest.raises(ValidationError):
        _ref(media_type="application/x-msdownload")


@pytest.mark.parametrize("hash_value", ["sha256:xyz", "sha256:" + "A" * 64, "md5:" + "a" * 32])
def test_hash_format_enforced(hash_value: str) -> None:
    with pytest.raises(ValidationError):
        _ref(revision_body_hash=hash_value)


def test_size_bounds() -> None:
    with pytest.raises(ValidationError):
        _ref(size=0)
    with pytest.raises(ValidationError):
        _ref(size=25 * 1024 * 1024 + 1)
