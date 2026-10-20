"""LLM model API schemas with explicit legacy and canonical price units."""

from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

Price = Optional[Decimal]


def _validate_price_representations(model):
    for legacy_name, canonical_name in (
        ("input_cost_per_1k", "input_cost_per_1m"),
        ("output_cost_per_1k", "output_cost_per_1m"),
    ):
        legacy = getattr(model, legacy_name)
        canonical = getattr(model, canonical_name)
        if legacy is not None and canonical is not None and legacy * Decimal(1000) != canonical:
            raise ValueError(f"{legacy_name} and {canonical_name} describe conflicting prices")
        if legacy_name in model.model_fields_set and legacy is None:
            raise ValueError(f"{legacy_name} cannot be null; omit it to preserve the existing price")
        if canonical_name in model.model_fields_set and canonical is None:
            raise ValueError(f"{canonical_name} cannot be null; omit it to preserve the existing price")
    return model


class LLMModelBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., max_length=100)
    model_id: str = Field(..., max_length=100)
    description: Optional[str] = None
    provider_id: int
    model_type: str = Field(..., pattern="^(text|image|embedding|decision)$")
    custom_endpoint_path: Optional[str] = Field(None, description="Custom endpoint path for api_format=custom")
    pricing_token_unit: Literal[1000, 1000000] = Field(
        1000000, description="Unit used by raw catalog input_cost_per_1k/output_cost_per_1k fields"
    )
    # Public compatibility fields are mathematically USD per 1K tokens.
    input_cost_per_1k: Price = Field(None, ge=0, allow_inf_nan=False, description="Legacy public USD per 1K")
    output_cost_per_1k: Price = Field(None, ge=0, allow_inf_nan=False, description="Legacy public USD per 1K")
    # Canonical public rates are mathematically USD per 1M tokens.
    input_cost_per_1m: Price = Field(None, ge=0, allow_inf_nan=False)
    output_cost_per_1m: Price = Field(None, ge=0, allow_inf_nan=False)
    cache_read_cost_per_1m: Price = Field(None, ge=0, allow_inf_nan=False)
    cache_write_5m_cost_per_1m: Price = Field(None, ge=0, allow_inf_nan=False)
    cache_write_1h_cost_per_1m: Price = Field(None, ge=0, allow_inf_nan=False)
    max_tokens: int = 4096
    default_temperature: float = 0.3
    is_active: bool = True
    is_default: bool = False

    @model_validator(mode="after")
    def validate_price_representations(self):
        return _validate_price_representations(self)


class LLMModelCreate(LLMModelBase):
    pass


class LLMModelUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: Optional[str] = Field(None, max_length=100)
    model_id: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    model_type: Optional[str] = Field(None, pattern="^(text|image|embedding|decision)$")
    custom_endpoint_path: Optional[str] = Field(None, description="Custom endpoint path for api_format=custom")
    pricing_token_unit: Optional[Literal[1000, 1000000]] = None
    input_cost_per_1k: Price = Field(None, ge=0, allow_inf_nan=False)
    output_cost_per_1k: Price = Field(None, ge=0, allow_inf_nan=False)
    input_cost_per_1m: Price = Field(None, ge=0, allow_inf_nan=False)
    output_cost_per_1m: Price = Field(None, ge=0, allow_inf_nan=False)
    cache_read_cost_per_1m: Price = Field(None, ge=0, allow_inf_nan=False)
    cache_write_5m_cost_per_1m: Price = Field(None, ge=0, allow_inf_nan=False)
    cache_write_1h_cost_per_1m: Price = Field(None, ge=0, allow_inf_nan=False)
    max_tokens: Optional[int] = None
    default_temperature: Optional[float] = None
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None

    @model_validator(mode="after")
    def validate_price_representations(self):
        if "pricing_token_unit" in self.model_fields_set and self.pricing_token_unit is None:
            raise ValueError("pricing_token_unit cannot be null; omit it to preserve the current unit")
        return _validate_price_representations(self)


class LLMModelResponse(LLMModelBase):
    id: int
    input_cost_per_1k: Decimal
    output_cost_per_1k: Decimal
    input_cost_per_1m: Decimal
    output_cost_per_1m: Decimal
    last_used_at: Optional[str] = None
    last_success_at: Optional[str] = None
    last_error_at: Optional[str] = None
    use_count: int = 0
    fail_count: int = 0
    created_at: str
    updated_at: str
    custom_endpoint_path: Optional[str] = None


class LLMModelList(BaseModel):
    models: list[LLMModelResponse]
    total: int
