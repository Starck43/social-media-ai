"""Stable metadata-only response contracts for personal connector authorization.

Ignore unlisted service fields at the HTTP boundary: neither secret material nor
internal ownership/scope details belong in these responses. These are local
state facts, never a promise of provider health or a runtime execution permit.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer

PositiveId = Annotated[int, Field(strict=True, gt=0)]


class _MetadataResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ConnectorAccountMetadata(_MetadataResponse):
    authorization_id: PositiveId
    provider_id: str
    auth_method: str
    subject_key: str
    revision: PositiveId
    state: Literal["active", "reauth", "revoked"]


class WorkspaceGrantMetadata(_MetadataResponse):
    grant_id: PositiveId
    authorization_id: PositiveId
    authorization_revision: PositiveId
    is_active: bool


class ReusableConnectorAccounts(_MetadataResponse):
    accounts: list[ConnectorAccountMetadata]
    can_attach: bool


class TelegramLoginMetadata(_MetadataResponse):
    attempt_id: UUID
    state: Literal["processing", "code", "password", "completed", "failed", "expired", "cancelled"]
    revision: PositiveId
    expires_at: datetime = Field(json_schema_extra={"format": "date-time"})
    failure_code: str | None
    code_attempts_remaining: Annotated[int, Field(strict=True, ge=0)]
    password_attempts_remaining: Annotated[int, Field(strict=True, ge=0)]
    authorization_id: PositiveId | None
    grant_id: PositiveId | None

    @field_serializer("expires_at")
    def serialize_expires_at(self, value: datetime) -> str:
        # Preserve the existing jsonable_encoder datetime spelling, including
        # +00:00 rather than changing UTC timestamps to Pydantic's default Z.
        return value.isoformat()


class ConnectorAccountError(_MetadataResponse):
    detail: str
