"""Strict input for revision-bound document approvals."""

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, Field, StrictInt, field_validator

from app.schemas.business import BusinessSchema


class ApprovalRequest(BusinessSchema):
    approver_user_id: StrictInt = Field(gt=0)
    expected_revision_id: StrictInt = Field(gt=0)
    expected_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expires_at: AwareDatetime
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("idempotency_key")
    @classmethod
    def key_valid(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("invalid_key")
        return value


class ApprovalDecision(BusinessSchema):
    decision: Literal["approved", "rejected", "revision_requested"]
    comment: str | None = Field(default=None, max_length=4000)

    @field_validator("comment")
    @classmethod
    def comment_valid(cls, value):
        if value is not None and "\x00" in value:
            raise ValueError("invalid_comment")
        return value


class ApprovalOut(BusinessSchema):
    id: int
    document_id: int
    revision_id: int
    revision_hash: str
    requester_user_id: int | None
    approver_user_id: int | None
    status: Literal["pending", "approved", "rejected", "revision_requested", "expired"]
    requested_at: datetime
    responded_at: datetime | None
    expires_at: datetime


class ApprovalComment(BusinessSchema):
    comment: str | None = Field(default=None, max_length=4000)

    @field_validator("comment")
    @classmethod
    def comment_valid(cls, value):
        if value is not None and "\x00" in value:
            raise ValueError("invalid_comment")
        return value
