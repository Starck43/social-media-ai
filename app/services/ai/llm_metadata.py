"""
LLM metadata helpers — reads provider/model info from DB at runtime.
No hardcoded presets; everything comes from llm_providers / llm_models tables.
"""
from decimal import Decimal
from typing import Any, Optional

from app.models import LLMModel, LLMProvider


async def get_model_info(model_id_or_name: str) -> Optional[dict[str, Any]]:
    """Look up model by name or model_id, return dict with provider + model details."""
    model = await LLMModel.objects.select_related("provider").get(name=model_id_or_name)
    if not model:
        model = await LLMModel.objects.select_related("provider").get(model_id=model_id_or_name)
    if not model:
        return None
    return _model_dict(model)


async def list_active_models() -> list[dict[str, Any]]:
    models = await LLMModel.objects.select_related("provider").filter(is_active=True).order_by("id").all()
    return [_model_dict(m) for m in models]


async def get_provider_models(provider_id: int) -> list[dict[str, Any]]:
    models = await LLMModel.objects.select_related("provider").filter(provider_id=provider_id, is_active=True).all()
    return [_model_dict(m) for m in models]


def _model_dict(m: LLMModel) -> dict:
    unit = getattr(m, "pricing_token_unit", 1000) or 1000
    input_raw = Decimal(str(m.input_cost_per_1k))
    output_raw = Decimal(str(m.output_cost_per_1k))
    input_per_1k = input_raw * Decimal(1000) / Decimal(unit)
    output_per_1k = output_raw * Decimal(1000) / Decimal(unit)
    input_per_1m = input_raw * Decimal(1_000_000) / Decimal(unit)
    output_per_1m = output_raw * Decimal(1_000_000) / Decimal(unit)
    return {
        "id": m.id,
        "name": m.name,
        "model_id": m.model_id,
        "description": m.description,
        "model_type": m.model_type,
        "provider_id": m.provider_id,
        "provider_name": m.provider.name,
        "provider_format": m.provider.api_format,
        "base_url": m.provider.base_url,
        "input_cost_per_1k": input_per_1k,
        "output_cost_per_1k": output_per_1k,
        "input_cost_per_1m": input_per_1m,
        "output_cost_per_1m": output_per_1m,
        "pricing_token_unit": unit,
        "cache_read_cost_per_1m": m.cache_read_cost_per_1m,
        "cache_write_5m_cost_per_1m": m.cache_write_5m_cost_per_1m,
        "cache_write_1h_cost_per_1m": m.cache_write_1h_cost_per_1m,
        "max_tokens": m.max_tokens,
        "default_temperature": m.default_temperature,
        "is_active": m.is_active,
        "is_default": m.is_default,
    }
