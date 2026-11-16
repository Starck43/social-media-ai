"""Strict contracts for document export and document email delivery (P19-T05).

The export response is a base64 envelope, not a binary stream: business routes
must declare an explicit response model (`BusinessRoute`), and no binary
download precedent exists in this API. The envelope carries the same evidence
the spool reference does (hash, size, revision) so a client can verify bytes.
"""

from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, Field, StrictInt, field_validator

from app.schemas.business import BusinessSchema
from app.services.communications.outbox_contracts import TargetIn


class ExportRequestIn(BusinessSchema):
    """Optional staleness guard: refuse when the current revision moved on."""

    expected_revision_id: StrictInt | None = Field(default=None, gt=0)
    expected_content_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")


class ExportFileOut(BusinessSchema):
    filename: str
    media_type: str
    size: StrictInt = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision_id: StrictInt = Field(gt=0)
    revision_body_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    content_b64: str


class SendDocumentEmailIn(BusinessSchema):
    """One email target; attachment-capable channels only (email today).

    Field constraints mirror `DraftIn` exactly so the outbox draft never
    rejects what this contract accepted.
    """

    format: Literal["docx", "pdf"]
    contact_id: StrictInt = Field(gt=0)
    subject: str = Field(default="", max_length=998)
    body: str = Field(min_length=1, max_length=1000000)
    purpose: Literal["transactional", "greeting", "marketing"] = "transactional"
    timezone: str = "UTC"
    target: TargetIn
    scheduled_at: AwareDatetime
    ttl_seconds: StrictInt = Field(default=86400, ge=60, le=86400)

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("invalid_timezone") from None
        return value

    @field_validator("subject", "body")
    @classmethod
    def valid_text(cls, value):
        if "\x00" in value:
            raise ValueError("invalid_text") from None
        return value
