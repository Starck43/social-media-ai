"""Agent tools for LLM model/provider lifecycle.

All write tools require confirmation. Delete shows staging preview with
reassignment plan before committing.
"""

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from app.agent.tools import tool
from app.models import AgentScenario, LLMModel, LLMProvider
from app.models.managers.llm_model_manager import LLMModelManager
from app.services.ai.llm_client import LLMClientFactory, journal_chat_attempt

logger = logging.getLogger(__name__)
_UNSET = object()


def _public_model_prices(model: LLMModel) -> dict[str, Any]:
    unit = model.pricing_token_unit or 1000
    input_raw = Decimal(str(model.input_cost_per_1k))
    output_raw = Decimal(str(model.output_cost_per_1k))
    return {
        "pricing_token_unit": unit,
        "input_cost_per_1k": float(input_raw * Decimal(1000) / Decimal(unit)),
        "output_cost_per_1k": float(output_raw * Decimal(1000) / Decimal(unit)),
        "input_cost_per_1m": float(input_raw * Decimal(1_000_000) / Decimal(unit)),
        "output_cost_per_1m": float(output_raw * Decimal(1_000_000) / Decimal(unit)),
        "cache_read_cost_per_1m": (
            float(model.cache_read_cost_per_1m) if model.cache_read_cost_per_1m is not None else None
        ),
        "cache_write_5m_cost_per_1m": (
            float(model.cache_write_5m_cost_per_1m) if model.cache_write_5m_cost_per_1m is not None else None
        ),
        "cache_write_1h_cost_per_1m": (
            float(model.cache_write_1h_cost_per_1m) if model.cache_write_1h_cost_per_1m is not None else None
        ),
    }


def _raw_price(legacy_per_1k, canonical_per_1m, unit: int) -> float | None:
    if legacy_per_1k is None or legacy_per_1k is _UNSET:
        legacy_per_1k = None
    if canonical_per_1m is None or canonical_per_1m is _UNSET:
        canonical_per_1m = None
    if legacy_per_1k is not None and canonical_per_1m is not None:
        if Decimal(str(legacy_per_1k)) * Decimal(1000) != Decimal(str(canonical_per_1m)):
            raise ValueError("per-1K and per-1M prices conflict")
    amount = Decimal(str(canonical_per_1m)) if canonical_per_1m is not None else None
    if amount is None and legacy_per_1k is not None:
        amount = Decimal(str(legacy_per_1k)) * Decimal(1000)
    if amount is None:
        return None
    if not amount.is_finite() or amount < 0:
        raise ValueError("Model prices must be finite and non-negative")
    return float(amount * Decimal(unit) / Decimal(1_000_000))


def _cache_price(value):
    if value is None:
        return None
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Cache prices must be finite and non-negative")
    return amount


# ──────────────────────────────────────────────────────────────
# Read tools
# ──────────────────────────────────────────────────────────────


@tool(
    name="llm_providers_list",
    description="List all LLM providers",
    parameters={
        "type": "object",
        "properties": {
            "active_only": {"type": "boolean", "default": True, "description": "Filter only active providers"}
        },
    },
    required_permission="llm.model.view",
)
async def llm_providers_list(active_only: bool = True) -> list[dict[str, Any]]:
    """List all LLM providers."""
    qs = LLMProvider.objects.filter(is_active=True) if active_only else LLMProvider.objects.all()
    providers = await qs.order_by("name")
    return [
        {
            "id": p.id,
            "name": p.name,
            "api_format": p.api_format,
            "base_url": p.base_url,
            "is_active": p.is_active,
        }
        for p in providers
    ]


@tool(
    name="llm_models_list",
    description="List LLM models with optional filters",
    parameters={
        "type": "object",
        "properties": {
            "provider_id": {"type": "integer", "description": "Filter by provider"},
            "model_type": {
                "type": "string",
                "enum": ["text", "image", "embedding", "decision"],
                "description": "Filter by model type",
            },
            "active_only": {"type": "boolean", "default": True, "description": "Filter only active models"},
        },
    },
    required_permission="llm.model.view",
)
async def llm_models_list(
    provider_id: int | None = None,
    model_type: str | None = None,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    """List LLM models with optional filters."""
    filters = {}
    if provider_id is not None:
        filters["provider_id"] = provider_id
    if model_type is not None:
        filters["model_type"] = model_type
    if active_only:
        filters["is_active"] = True

    models = await LLMModel.objects.select_related("provider").filter(**filters).order_by("name")
    return [
        {
            "id": m.id,
            "name": m.name,
            "model_id": m.model_id,
            "provider": m.provider.name if m.provider else None,
            "provider_id": m.provider_id,
            "model_type": m.model_type,
            "custom_endpoint_path": m.custom_endpoint_path,
            "max_tokens": m.max_tokens,
            **_public_model_prices(m),
            "default_temperature": m.default_temperature,
            "is_active": m.is_active,
            "is_default": m.is_default,
            "last_used_at": m.last_used_at.isoformat() if m.last_used_at else None,
            "last_success_at": m.last_success_at.isoformat() if m.last_success_at else None,
            "last_error_at": m.last_error_at.isoformat() if m.last_error_at else None,
            "use_count": m.use_count,
            "fail_count": m.fail_count,
        }
        for m in models
    ]


@tool(
    name="llm_model_test",
    description="Test a model connection (no secrets returned)",
    parameters={
        "type": "object",
        "properties": {
            "model_id": {"type": "integer", "description": "Model ID to test"},
            "prompt": {"type": "string", "default": "Привет! Расскажи о себе кратко.", "description": "Test prompt"},
        },
        "required": ["model_id"],
    },
    required_permission="llm.model.view",
)
async def llm_model_test(model_id: int, prompt: str = "Привет! Расскажи о себе кратко.") -> dict[str, Any]:
    """Test a model connection (reuses admin test logic, no secrets exposed)."""
    model = await LLMModel.objects.select_related("provider").get(id=model_id)
    if not model:
        return {"ok": False, "error": f"Model {model_id} not found"}

    if not model.provider or not model.provider.is_active:
        return {"ok": False, "error": "Provider not found or inactive"}

    try:
        client = LLMClientFactory.create(model)
        try:
            response = await client.chat([{"role": "user", "content": prompt}], max_tokens=200)
        except Exception:
            await journal_chat_attempt(client, "model_test", None)
            raise
        await journal_chat_attempt(client, "model_test", response)
        content = response.get("content") or ""
        usage = response.get("usage", {})
        return {
            "ok": True,
            "model": model.name,
            "provider": model.provider.name,
            "response_preview": content[:200],
            "usage": usage,
        }
    except Exception as e:
        logger.warning(f"Model test failed for {model_id}: {e}")
        return {"ok": False, "error": str(e)}


# ──────────────────────────────────────────────────────────────
# Write tools (confirm=True)
# ──────────────────────────────────────────────────────────────


@tool(
    name="llm_model_add",
    description="Create a new LLM model (requires confirmation)",
    confirm=True,
    required_permission="llm.model.create",
    parameters={
        "type": "object",
        "properties": {
            "provider_id": {"type": "integer", "description": "Provider ID"},
            "name": {"type": "string", "description": "Human name, e.g. GPT-4 Turbo"},
            "api_model_id": {"type": "string", "description": "API model identifier, e.g. gpt-4-turbo"},
            "model_type": {"type": "string", "enum": ["text", "image", "embedding", "decision"], "default": "text"},
            "description": {"type": "string"},
            "custom_endpoint_path": {"type": "string", "description": "Endpoint path for custom-format providers"},
            "pricing_token_unit": {
                "type": "integer",
                "enum": [1000, 1000000],
                "default": 1000000,
                "description": "Raw catalog unit for input/output rates",
            },
            "input_cost_per_1k": {"type": "number", "description": "Legacy public USD per 1K rate"},
            "output_cost_per_1k": {"type": "number", "description": "Legacy public USD per 1K rate"},
            "input_cost_per_1m": {"type": "number", "description": "Canonical public USD per 1M rate"},
            "output_cost_per_1m": {"type": "number", "description": "Canonical public USD per 1M rate"},
            "cache_read_cost_per_1m": {"type": "number", "description": "USD per 1M cache-read tokens"},
            "cache_write_5m_cost_per_1m": {
                "type": "number",
                "description": "USD per 1M five-minute cache-write tokens",
            },
            "cache_write_1h_cost_per_1m": {
                "type": "number",
                "description": "USD per 1M one-hour cache-write tokens",
            },
            "max_tokens": {"type": "integer", "default": 4096},
            "default_temperature": {"type": "number", "default": 0.3},
            "is_active": {"type": "boolean", "default": True},
            "is_default": {"type": "boolean", "default": False},
        },
        "required": ["provider_id", "name", "api_model_id"],
    },
)
async def llm_model_add(
    *,
    provider_id: int,
    name: str,
    api_model_id: str,
    model_type: str = "text",
    description: str | None = None,
    custom_endpoint_path: str | None = None,
    pricing_token_unit: int = 1_000_000,
    input_cost_per_1k: float | None = None,
    output_cost_per_1k: float | None = None,
    input_cost_per_1m: float | None = None,
    output_cost_per_1m: float | None = None,
    cache_read_cost_per_1m: float | None = None,
    cache_write_5m_cost_per_1m: float | None = None,
    cache_write_1h_cost_per_1m: float | None = None,
    max_tokens: int = 4096,
    default_temperature: float = 0.3,
    is_active: bool = True,
    is_default: bool = False,
) -> dict[str, Any]:
    """Create a new LLM model. Requires confirmation."""
    try:
        if pricing_token_unit not in {1000, 1_000_000}:
            return {"error": "create_failed", "message": "pricing_token_unit must be 1000 or 1000000"}
        raw_input = _raw_price(input_cost_per_1k, input_cost_per_1m, pricing_token_unit)
        raw_output = _raw_price(output_cost_per_1k, output_cost_per_1m, pricing_token_unit)
        model = await LLMModel.objects.create_model(
            provider_id=provider_id,
            name=name,
            model_id=api_model_id,
            model_type=model_type,
            description=description,
            custom_endpoint_path=custom_endpoint_path,
            input_cost_per_1k=raw_input if raw_input is not None else 0.0,
            output_cost_per_1k=raw_output if raw_output is not None else 0.0,
            pricing_token_unit=pricing_token_unit,
            cache_read_cost_per_1m=_cache_price(cache_read_cost_per_1m),
            cache_write_5m_cost_per_1m=_cache_price(cache_write_5m_cost_per_1m),
            cache_write_1h_cost_per_1m=_cache_price(cache_write_1h_cost_per_1m),
            max_tokens=max_tokens,
            default_temperature=default_temperature,
            is_active=is_active,
            is_default=is_default,
        )
    except ValueError as e:
        message = str(e)
        if "not found" in message:
            return {"error": "provider_not_found", "message": message}
        if "not active" in message:
            return {"error": "provider_inactive", "message": message}
        return {"error": "create_failed", "message": message}
    logger.info(f"Agent created LLM model {model.name} (ID: {model.id})")
    return {
        "id": model.id,
        "name": model.name,
        "model_id": model.model_id,
        "model_type": model.model_type,
        "is_default": model.is_default,
        **_public_model_prices(model),
    }


@tool(
    name="llm_model_update",
    description="Update an LLM model (requires confirmation)",
    confirm=True,
    required_permission="llm.model.update",
    parameters={
        "type": "object",
        "properties": {
            "model_id": {"type": "integer", "description": "Model ID to update"},
            "name": {"type": "string"},
            "api_model_id": {"type": "string"},
            "model_type": {"type": "string", "enum": ["text", "image", "embedding", "decision"]},
            "description": {"type": "string"},
            "custom_endpoint_path": {"type": "string", "description": "Endpoint path for custom-format providers"},
            "pricing_token_unit": {"type": "integer", "enum": [1000, 1000000]},
            "input_cost_per_1k": {"type": "number"},
            "output_cost_per_1k": {"type": "number"},
            "input_cost_per_1m": {"type": "number"},
            "output_cost_per_1m": {"type": "number"},
            "cache_read_cost_per_1m": {"type": ["number", "null"]},
            "cache_write_5m_cost_per_1m": {"type": ["number", "null"]},
            "cache_write_1h_cost_per_1m": {"type": ["number", "null"]},
            "max_tokens": {"type": "integer"},
            "default_temperature": {"type": "number"},
            "is_active": {"type": "boolean"},
            "is_default": {"type": "boolean"},
        },
        "required": ["model_id"],
    },
)
async def llm_model_update(
    model_id: int,
    *,
    name: str | None = None,
    api_model_id: str | None = None,
    model_type: str | None = None,
    description: str | None = None,
    custom_endpoint_path: str | None = None,
    pricing_token_unit: int | None = _UNSET,
    input_cost_per_1k: float | None = _UNSET,
    output_cost_per_1k: float | None = _UNSET,
    input_cost_per_1m: float | None = _UNSET,
    output_cost_per_1m: float | None = _UNSET,
    cache_read_cost_per_1m: float | None = _UNSET,
    cache_write_5m_cost_per_1m: float | None = _UNSET,
    cache_write_1h_cost_per_1m: float | None = _UNSET,
    max_tokens: int | None = None,
    default_temperature: float | None = None,
    is_active: bool | None = None,
    is_default: bool | None = None,
) -> dict[str, Any]:
    """Partially update an LLM model. Requires confirmation."""
    model = await LLMModel.objects.get(id=model_id)
    if not model:
        return {"error": "model_not_found", "message": f"Model {model_id} not found"}

    if pricing_token_unit is None:
        return {"error": "update_failed", "message": "pricing_token_unit cannot be null"}
    target_unit = pricing_token_unit if pricing_token_unit is not _UNSET else model.pricing_token_unit
    if target_unit not in {1000, 1_000_000}:
        return {"error": "update_failed", "message": "pricing_token_unit must be 1000 or 1000000"}

    updates = {}
    if name is not None:
        updates["name"] = name
    if api_model_id is not None:
        updates["model_id"] = api_model_id
    if model_type is not None:
        updates["model_type"] = model_type
    if description is not None:
        updates["description"] = description
    if custom_endpoint_path is not None:
        updates["custom_endpoint_path"] = custom_endpoint_path
    try:
        if any(
            value is None
            for value in (input_cost_per_1k, output_cost_per_1k, input_cost_per_1m, output_cost_per_1m)
        ):
            return {"error": "update_failed", "message": "Ordinary prices cannot be null; omit to preserve them"}
        raw_input = _raw_price(input_cost_per_1k, input_cost_per_1m, target_unit)
        raw_output = _raw_price(output_cost_per_1k, output_cost_per_1m, target_unit)
        if raw_input is not None:
            updates["input_cost_per_1k"] = raw_input
        if raw_output is not None:
            updates["output_cost_per_1k"] = raw_output
        for field, value in (
            ("cache_read_cost_per_1m", cache_read_cost_per_1m),
            ("cache_write_5m_cost_per_1m", cache_write_5m_cost_per_1m),
            ("cache_write_1h_cost_per_1m", cache_write_1h_cost_per_1m),
        ):
            if value is not _UNSET:
                updates[field] = _cache_price(value)
    except (InvalidOperation, ValueError) as exc:
        return {"error": "update_failed", "message": str(exc)}
    if pricing_token_unit is not _UNSET:
        updates["pricing_token_unit"] = target_unit
    if max_tokens is not None:
        updates["max_tokens"] = max_tokens
    if default_temperature is not None:
        updates["default_temperature"] = default_temperature
    if is_active is not None:
        updates["is_active"] = is_active
    if is_default is not None:
        updates["is_default"] = is_default

    if not updates:
        return {"error": "no_changes", "message": "No fields to update"}

    updated = await LLMModel.objects.update_model(model_id, **updates)
    if not updated:
        return {"error": "model_not_found", "message": f"Model {model_id} not found"}
    logger.info(f"Agent updated LLM model {updated.name} (ID: {updated.id})")
    return {
        "id": updated.id,
        "name": updated.name,
        "model_id": updated.model_id,
        "model_type": updated.model_type,
        "is_default": updated.is_default,
        **_public_model_prices(updated),
    }


@tool(
    name="llm_model_delete",
    description="Delete an LLM model with default reassignment preview (requires confirmation)",
    confirm=True,
    required_permission="llm.model.delete",
    parameters={
        "type": "object",
        "properties": {
            "model_id": {"type": "integer", "description": "Model ID to delete"},
            "dry_run": {"type": "boolean", "default": True, "description": "Show plan without committing"},
        },
        "required": ["model_id"],
    },
)
async def llm_model_delete(
    model_id: int,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Delete an LLM model with default reassignment.

    In dry_run mode (default), shows the reassignment plan without committing.
    With dry_run=false, performs the deletion.
    """
    model = await LLMModel.objects.select_related("provider").get(id=model_id)
    if not model:
        return {"error": "model_not_found", "message": f"Model {model_id} not found"}

    mgr = LLMModelManager()

    was_default = model.is_default
    model_type = model.model_type

    # Count scenarios that reference this model
    scenarios_reset = 0
    for field_name in ("text_llm_model_id", "image_llm_model_id", "video_llm_model_id"):
        count = await AgentScenario.objects.filter(**{field_name: model_id}).count()
        if count:
            scenarios_reset += count

    candidate = None
    if was_default:
        candidate = await mgr._find_default_candidate(model_type, exclude_id=model_id)

    plan = {
        "deleted_model": {
            "id": model.id,
            "name": model.name,
            "model_type": model.model_type,
            "was_default": was_default,
        },
        "scenarios_reset": scenarios_reset,
        "new_default": (
            {
                "id": candidate.id,
                "name": candidate.name,
                "model_type": candidate.model_type,
                "last_success_at": candidate.last_success_at.isoformat() if candidate.last_success_at else None,
            }
            if candidate
            else None
        ),
        "warnings": [f"no model left for type {model_type}"] if was_default and not candidate else [],
    }

    if dry_run:
        return {"dry_run": True, "plan": plan}

    # Execute deletion with reassignment
    result = await mgr.delete_with_default_reassignment(model_id)
    logger.info(f"Agent deleted LLM model {model_id}, reassignment: {result}")
    return {"dry_run": False, "result": result}
