"""
Schemas for LLM Provider management.
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class LLMProviderCreate(BaseModel):
	"""Schema for creating a new LLM provider."""
	name: str = Field(..., min_length=1, max_length=255, description="Provider name")
	description: Optional[str] = Field(None, description="Provider description")
	provider_type: str = Field(..., description="Provider type (deepseek, openai, etc.)")
	api_url: str = Field(..., description="API endpoint URL")
	api_key_env: str = Field(..., description="Environment variable name for API key")
	model_name: str = Field(..., description="Model name to use")
	capabilities: List[str] = Field(default_factory=list, description="Capabilities: text, image, video")
	config: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional configuration")
	is_active: bool = Field(default=True, description="Whether provider is active")


class LLMProviderUpdate(BaseModel):
	"""Schema for updating an LLM provider."""
	name: Optional[str] = Field(None, min_length=1, max_length=255)
	description: Optional[str] = None
	provider_type: Optional[str] = None
	api_url: Optional[str] = None
	api_key_env: Optional[str] = None
	model_name: Optional[str] = None
	capabilities: Optional[List[str]] = None
	config: Optional[Dict[str, Any]] = None
	is_active: Optional[bool] = None


class LLMProviderResponse(BaseModel):
	"""Schema for LLM provider response."""
	id: int
	name: str
	description: Optional[str]
	provider_type: str
	api_url: str
	api_key_env: str
	model_name: str
	capabilities: List[str]
	config: Dict[str, Any]
	is_active: bool
	created_at: str
	updated_at: str

	class Config:
		from_attributes = True


class LLMProviderList(BaseModel):
	"""Schema for list of LLM providers."""
	providers: List[LLMProviderResponse]
	total: int
