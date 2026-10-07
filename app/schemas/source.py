"""Schemas for Source API endpoints."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class SourceCreate(BaseModel):
    """Request body for creating a source."""

    name: str = Field(..., min_length=1, max_length=255)
    platform_id: int
    source_type: str
    external_id: str = Field(..., max_length=100)
    params: dict[str, Any] = Field(default_factory=dict)


class SourceUpdate(BaseModel):
    """Request body for updating a source — only provided fields are changed."""

    name: Optional[str] = Field(None, max_length=255)
    params: Optional[dict[str, Any]] = None
    is_active: Optional[bool] = None


class SourceResponse(BaseModel):
    """Response body for a source."""

    id: int
    name: str
    platform_id: int
    source_type: Any
    external_id: str
    params: dict[str, Any]
    is_active: bool
    last_checked: Optional[datetime] = None
    last_item_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("source_type", check_fields=False)
    def _serialize_source_type(self, value: Any) -> str:
        if isinstance(value, Enum):
            return value.name
        return str(value) if value else ""
