"""Schemas for LLM Provider management."""
from typing import Any, Optional

from pydantic import BaseModel, Field


class LLMProviderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    api_format: str = Field(default="openai", description="openai | anthropic")
    base_url: str = Field(..., description="Base API URL, e.g. https://api.openai.com/v1")
    auth_header: Optional[str] = Field(None, description='Custom auth header, e.g. "x-api-key: {key}"')
    is_active: bool = Field(default=True)
    is_default: bool = Field(default=False)


class LLMProviderUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    api_format: Optional[str] = None
    base_url: Optional[str] = None
    auth_header: Optional[str] = None
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None


class LLMProviderResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    api_format: str
    base_url: str
    auth_header: Optional[str]
    is_active: bool
    is_default: bool
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class LLMProviderList(BaseModel):
    providers: list[LLMProviderResponse]
    total: int
