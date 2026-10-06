"""Schemas for Credential API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class CredentialResponse(BaseModel):
    """Response body for a credential (secrets are never returned)."""

    id: int
    user_id: int
    platform: str
    kind: str
    label: Optional[str] = None
    expires_at: Optional[datetime] = None
    is_active: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class CredentialLoginRequest(BaseModel):
    """Request body for interactive login."""

    platform: str = Field(..., pattern="^telegram$")
    api_id: Optional[int] = None
    api_hash: Optional[str] = None


class CredentialOauthRequest(BaseModel):
    """Request body for OAuth flow."""

    platform: str = Field(..., pattern="^vk$")
