"""
LLM metadata helpers — reads provider/model info from DB at runtime.
No hardcoded presets; everything comes from llm_providers / llm_models tables.
"""

from decimal import Decimal
from typing import Any, Optional

from app.models import LLMModel, LLMProvider
from app.services.ai.provider_egress import provider_url_metadata


async def get_model_info(model_id_or_name: str) -> Optional[dict[str, Any]]:
    """Look up model by name or model_id, return dict with provider + model details."""
    from app.services.ai.catalog_reads import current_catalog_reads

    scope = current_catalog_reads()
    if scope is not None:
        rows = await scope.metadata(capabilities={"text"})
        matches = [row for row in rows if model_id_or_name in {row.name, row.model_id}]
        return _catalog_metadata_dict(matches[0]) if len(matches) == 1 else None
    model = await LLMModel.objects.select_related("provider").get(name=model_id_or_name)
    if not model:
        model = await LLMModel.objects.select_related("provider").get(model_id=model_id_or_name)
    if not model:
        return None
    return _model_dict(model)


async def list_active_models() -> list[dict[str, Any]]:
    from app.services.ai.catalog_reads import current_catalog_reads

    scope = current_catalog_reads()
    if scope is not None:
        return [_catalog_metadata_dict(row) for row in await scope.metadata(capabilities={"text"})]
    models = await LLMModel.objects.select_related("provider").filter(is_active=True).order_by("id").all()
    return [_model_dict(m) for m in models]


async def get_provider_models(provider_id: int) -> list[dict[str, Any]]:
    from app.services.ai.catalog_reads import current_catalog_reads

    scope = current_catalog_reads()
    if scope is not None:
        return [
            _catalog_metadata_dict(row)
            for row in await scope.metadata(capabilities={"text"})
            if row.provider_id == provider_id
        ]
    models = await LLMModel.objects.select_related("provider").filter(provider_id=provider_id, is_active=True).all()
    return [_model_dict(m) for m in models]


def _catalog_metadata_dict(row) -> dict:
    from dataclasses import asdict

    return asdict(row)


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
        "base_url": provider_url_metadata(m.provider.base_url),
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
