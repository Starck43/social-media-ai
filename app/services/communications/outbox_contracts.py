"""Strict outbox API inputs; clients never supply route/approval/consent flags."""

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, Field, StrictInt, field_validator, model_validator

from app.schemas.business import BusinessSchema


class TargetIn(BusinessSchema):
    channel: Literal["email", "telegram", "max"]
    connection_id: StrictInt = Field(gt=0)
    identity_id: StrictInt = Field(gt=0)


class DraftIn(BusinessSchema):
    contact_id: StrictInt = Field(gt=0)
    subject: str = Field(default="", max_length=998)
    body: str = Field(min_length=1, max_length=1000000)
    purpose: Literal["transactional", "greeting", "marketing"] = "transactional"
    timezone: str = "UTC"
    targets: list[TargetIn] = Field(min_length=1, max_length=20)

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
            raise ValueError("invalid_text")
        return value

    @model_validator(mode="after")
    def distinct_targets(self):
        keys = [(t.channel, t.connection_id, t.identity_id) for t in self.targets]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate_target")
        return self


class ScheduleIn(BusinessSchema):
    message_id: StrictInt = Field(gt=0)
    expected_content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    scheduled_at: AwareDatetime
    ttl_seconds: StrictInt = Field(default=86400, ge=60, le=86400)


class TargetOut(BusinessSchema):
    id: int
    channel: str
    connection_id: int
    identity_id: int
    recipient: str
    state: str
    attempts: int
    command_id: int | None
    provider_message_id: str | None
    error_code: str | None


class MessageOut(BusinessSchema):
    id: int
    contact_id: int
    author_id: int
    subject: str
    body: str
    content_hash: str
    state: str
    timezone: str
    scheduled_at: datetime | None
    approval_expires_at: datetime | None
    targets: list[TargetOut]
