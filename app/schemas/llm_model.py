"""
Schemas for LLM Model API.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class LLMModelBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., max_length=100)
    model_id: str = Field(..., max_length=100)
    description: Optional[str] = None
    provider_id: int
    model_type: str = Field(..., pattern="^(text|image|embedding)$")
    input_cost_per_1k: float = 0.0
    output_cost_per_1k: float = 0.0
    max_tokens: int = 4096
    default_temperature: float = 0.3
    is_active: bool = True
    is_default: bool = False


class LLMModelCreate(LLMModelBase):
    pass


class LLMModelUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: Optional[str] = Field(None, max_length=100)
    model_id: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    model_type: Optional[str] = Field(None, pattern="^(text|image|embedding)$")
    input_cost_per_1k: Optional[float] = None
    output_cost_per_1k: Optional[float] = None
    max_tokens: Optional[int] = None
    default_temperature: Optional[float] = None
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None


class LLMModelResponse(LLMModelBase):
    id: int
    last_used_at: Optional[str] = None
    last_success_at: Optional[str] = None
    last_error_at: Optional[str] = None
    use_count: int = 0
    fail_count: int = 0
    created_at: str
    updated_at: str


class LLMModelList(BaseModel):
    models: list[LLMModelResponse]
    total: int