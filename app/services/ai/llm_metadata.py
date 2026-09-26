"""
LLM metadata helpers — reads provider/model info from DB at runtime.
No hardcoded presets; everything comes from llm_providers / llm_models tables.
"""
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
        "input_cost_per_1k": m.input_cost_per_1k,
        "output_cost_per_1k": m.output_cost_per_1k,
        "max_tokens": m.max_tokens,
        "default_temperature": m.default_temperature,
        "is_active": m.is_active,
        "is_default": m.is_default,
    }