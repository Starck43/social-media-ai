"""Transport-neutral attachment contract for the outbox (P15-T14).

An attachment never carries caller bytes: it references a stored document revision and
seals both the approved body (``revision_body_hash``) and the exact delivered file
(``attachment_bytes_hash``). Scheduling refuses anything the target channel cannot carry,
and dispatch re-verifies the delivered bytes before a MIME part is built. This module is
pure and DB-free: no file reads, no network, no provider send.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from email.message import EmailMessage

from pydantic import Field, StrictInt, field_validator

from app.schemas.business import BusinessSchema

SHA256_HEX = re.compile(r"^sha256:[0-9a-f]{64}$")


def _sha256(data: bytes) -> str:
    """Return the canonical ``sha256:<hex>`` digest used by every hash in this contract."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


ATTACHMENT_MEDIA_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
    }
)
"""Small allowlist: only renderable documents may leave as attachments."""

MAX_ATTACHMENTS = 10
MAX_TOTAL_BYTES = 25 * 1024 * 1024
MAX_FILENAME_LENGTH = 200
"""Bounds mirroring the outbox body limits; enforced without reading the files."""

ATTACHMENT_CHANNELS: frozenset[str] = frozenset({"email"})
"""Channels that can carry an attachment. Everything else fails closed."""


def channel_supports_attachments(channel: str) -> bool:
    """Return True only for a known channel with attachment transport (email).

    Unknown or non-email channels (``telegram``, ``max``, arbitrary input) return False so
    that scheduling refuses them instead of silently dropping attachments at dispatch.
    """
    return channel in ATTACHMENT_CHANNELS


class AttachmentRejected(Exception):
    """Stable refusal raised when an attachment set or delivered bytes break the contract."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class AttachmentRef(BusinessSchema):
    """Reference to a stored, approved attachment revision; never caller-supplied bytes.

    The caller picks which stored evidence to attach but the hashes seal it: dispatch must
    prove that the delivered file matches ``attachment_bytes_hash`` while the approval
    still covers ``(revision_id, revision_body_hash)``.
    """

    evidence_id: StrictInt = Field(gt=0)
    revision_id: StrictInt = Field(gt=0)
    revision_body_hash: str = Field(pattern=SHA256_HEX.pattern)
    attachment_bytes_hash: str = Field(pattern=SHA256_HEX.pattern)
    size: StrictInt = Field(gt=0, le=MAX_TOTAL_BYTES)
    media_type: str
    filename: str = Field(min_length=1, max_length=MAX_FILENAME_LENGTH)

    @field_validator("media_type")
    @classmethod
    def allowed_media_type(cls, value: str) -> str:
        if value not in ATTACHMENT_MEDIA_TYPES:
            raise ValueError("unsupported_media_type")
        return value

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        """Reject names that could escape the save directory or inject MIME headers."""
        if not 1 <= len(value) <= MAX_FILENAME_LENGTH:
            raise ValueError("invalid_filename")
        if any(ch in value for ch in "\r\n\x00") or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
            raise ValueError("invalid_filename")
        if value.startswith(".") or any(sep in value for sep in ("/", "\\")):
            raise ValueError("invalid_filename")
        return value


def validate_attachment_set(refs: Iterable[AttachmentRef], channel: str) -> tuple[AttachmentRef, ...]:
    """Validate the ordered attachment set for a target channel and return it as a tuple.

    Order is meaningful and preserved. The set is all-or-nothing: any refusal means the item
    must not be scheduled at all rather than sent with fewer attachments.
    """
    items = tuple(refs)
    if not channel_supports_attachments(channel):
        raise AttachmentRejected("channel_cannot_attach", "channel does not transmit attachments")
    if not items:
        raise AttachmentRejected("no_attachments", "empty attachment set")
    if len(items) > MAX_ATTACHMENTS:
        raise AttachmentRejected("too_many_attachments", f"at most {MAX_ATTACHMENTS} attachments")
    if sum(item.size for item in items) > MAX_TOTAL_BYTES:
        raise AttachmentRejected("attachment_size_exceeded", "attachment set exceeds the total size budget")
    seen: set[tuple[int, str]] = set()
    rendered: dict[int, str] = {}
    for item in items:
        key = (item.revision_id, item.attachment_bytes_hash)
        if key in seen:
            raise AttachmentRejected("duplicate_attachment", "same revision and bytes attached twice")
        seen.add(key)
        previous = rendered.setdefault(item.revision_id, item.attachment_bytes_hash)
        if previous != item.attachment_bytes_hash:
            raise AttachmentRejected("revision_render_mismatch", "one revision must render exactly one file")
    return items


def verify_delivered_bytes(ref: AttachmentRef, delivered: bytes) -> None:
    """Re-verify that delivered bytes are the sealed file; pure and cheap (one hash).

    A re-render or corrupted payload after approval raises instead of being sent, so a
    mismatch never reaches the provider.
    """
    if len(delivered) != ref.size:
        raise AttachmentRejected("attachment_size_mismatch", "delivered size differs from the reference")
    if _sha256(delivered) != ref.attachment_bytes_hash:
        raise AttachmentRejected("attachment_hash_mismatch", "delivered bytes differ from the sealed hash")


def build_mime_part(ref: AttachmentRef, data: bytes) -> EmailMessage:
    """Verify the delivered bytes and return one leaf MIME part with the real media type.

    The caller must pass this to ``EmailMessage.add_attachment`` on a **multipart** parent.
    ``add_attachment`` wraps any non-``multipart/*`` child in ``message/rfc822``, so a part
    handed to it directly would reach the provider as an attached email instead of a file.
    No SMTP, filesystem or database access happens here.
    """
    verify_delivered_bytes(ref, data)
    maintype, _, subtype = ref.media_type.partition("/")
    part = EmailMessage()
    part.set_content(data, maintype=maintype, subtype=subtype, cte="base64")
    part.add_header("Content-Disposition", "attachment", filename=ref.filename)
    return part


def attach_to_message(message: EmailMessage, ref: AttachmentRef, data: bytes) -> None:
    """Add one verified attachment to an outgoing message without the ``message/rfc822`` trap.

    ``EmailMessage`` starts as ``text/plain``; ``add_attachment`` keeps a leaf child as
    ``message/rfc822`` unless the parent is already ``multipart/*``. This helper switches the
    parent to ``multipart/mixed`` first, preserving the existing body, then appends the part.
    The attachment set is all-or-nothing: one refusal here means nothing is sent.
    """
    part = build_mime_part(ref, data)
    if message.get_content_maintype() != "multipart":
        message.make_mixed()
    message.attach(part)
