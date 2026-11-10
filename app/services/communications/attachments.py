"""Transport-neutral references to transient spooled bytes; never caller bytes.

An approval's revision/body hash is a separate concern and must be rechecked by
its caller. The spool hash covers the exact file bytes and is not that body hash.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from email.message import EmailMessage

from pydantic import Field, StrictInt, field_validator

from app.schemas.business import BusinessSchema

SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
ATTACHMENT_MEDIA_TYPES = frozenset(
    {"application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "text/plain"}
)
ATTACHMENT_CHANNELS = frozenset({"email"})
MAX_ATTACHMENTS = 10
_EXTENSIONS = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/plain": "txt",
}


class AttachmentRejected(Exception):
    """Stable refusal; attachment errors never imply partial delivery."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def channel_supports_attachments(channel: str) -> bool:
    return channel in ATTACHMENT_CHANNELS


class AttachmentRef(BusinessSchema):
    """Only the exact spool reference. Tenant/item/position are outbox scope."""

    sha256: str = Field(pattern=SHA256_HEX.pattern)
    size: StrictInt = Field(gt=0)
    mime: str

    @field_validator("mime")
    @classmethod
    def allowed_media_type(cls, value: str) -> str:
        if value not in ATTACHMENT_MEDIA_TYPES:
            raise ValueError("unsupported_media_type")
        return value


def validate_attachment_set(refs: Iterable[AttachmentRef], channel: str) -> tuple[AttachmentRef, ...]:
    """Validate the ordered set using the deployment's current spool limits."""
    from app.core.config import settings

    items = tuple(refs)
    if not channel_supports_attachments(channel):
        raise AttachmentRejected("channel_cannot_attach")
    if not items:
        raise AttachmentRejected("no_attachments")
    if len(items) > MAX_ATTACHMENTS:
        raise AttachmentRejected("too_many_attachments")
    if any(ref.size > settings.ATTACHMENT_MAX_FILE_BYTES for ref in items):
        raise AttachmentRejected("attachment_too_large")
    if sum(ref.size for ref in items) > settings.ATTACHMENT_MAX_TOTAL_BYTES:
        raise AttachmentRejected("attachment_set_too_large")
    if len({ref.sha256 for ref in items}) != len(items):
        raise AttachmentRejected("duplicate_attachment")
    return items


def verify_delivered_bytes(ref: AttachmentRef, delivered: bytes) -> None:
    """Verify bytes again after the spool has returned them; do not re-render."""
    if len(delivered) != ref.size:
        raise AttachmentRejected("attachment_size_mismatch")
    if hashlib.sha256(delivered).hexdigest() != ref.sha256:
        raise AttachmentRejected("attachment_hash_mismatch")


def build_mime_part(ref: AttachmentRef, data: bytes) -> EmailMessage:
    """Build a real MIME leaf with a safe derived filename; never send here."""
    verify_delivered_bytes(ref, data)
    maintype, _, subtype = ref.mime.partition("/")
    part = EmailMessage()
    part.set_content(data, maintype=maintype, subtype=subtype, cte="base64")
    part.add_header("Content-Disposition", "attachment", filename=f"attachment-{ref.sha256[:16]}.{_EXTENSIONS[ref.mime]}")
    return part


def attach_to_message(message: EmailMessage, ref: AttachmentRef, data: bytes) -> None:
    part = build_mime_part(ref, data)
    if message.get_content_maintype() != "multipart":
        message.make_mixed()
    message.attach(part)
