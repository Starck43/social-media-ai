"""Agent tools for LLM model/provider lifecycle.

All write tools require confirmation. Delete shows staging preview with
reassignment plan before committing.
"""

import logging
from typing import Any

from app.agent.tools import tool
from app.models import AgentScenario, LLMModel, LLMProvider
from app.models.managers.llm_model_manager import LLMModelManager
from app.services.ai.llm_client import LLMClientFactory

logger = logging.getLogger(__name__)


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
    required_permission="llmmodel.view",
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
            "is_default": p.is_default,
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
    required_permission="llmmodel.view",
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
            "input_cost_per_1k": m.input_cost_per_1k,
            "output_cost_per_1k": m.output_cost_per_1k,
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
    required_permission="llmmodel.view",
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
        response = await client.chat([{"role": "user", "content": prompt}], max_tokens=200)
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
    required_permission="llmmodel.create",
    parameters={
        "type": "object",
        "properties": {
            "provider_id": {"type": "integer", "description": "Provider ID"},
            "name": {"type": "string", "description": "Human name, e.g. GPT-4 Turbo"},
            "api_model_id": {"type": "string", "description": "API model identifier, e.g. gpt-4-turbo"},
            "model_type": {"type": "string", "enum": ["text", "image", "embedding", "decision"], "default": "text"},
            "description": {"type": "string"},
            "custom_endpoint_path": {"type": "string", "description": "Endpoint path for custom-format providers"},
            "input_cost_per_1k": {"type": "number", "default": 0.0},
            "output_cost_per_1k": {"type": "number", "default": 0.0},
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
    input_cost_per_1k: float = 0.0,
    output_cost_per_1k: float = 0.0,
    max_tokens: int = 4096,
    default_temperature: float = 0.3,
    is_active: bool = True,
    is_default: bool = False,
) -> dict[str, Any]:
    """Create a new LLM model. Requires confirmation."""
    try:
        model = await LLMModel.objects.create_model(
            provider_id=provider_id,
            name=name,
            model_id=api_model_id,
            model_type=model_type,
            description=description,
            custom_endpoint_path=custom_endpoint_path,
            input_cost_per_1k=input_cost_per_1k,
            output_cost_per_1k=output_cost_per_1k,
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
    }


@tool(
    name="llm_model_update",
    description="Update an LLM model (requires confirmation)",
    confirm=True,
    required_permission="llmmodel.update",
    parameters={
        "type": "object",
        "properties": {
            "model_id": {"type": "integer", "description": "Model ID to update"},
            "name": {"type": "string"},
            "api_model_id": {"type": "string"},
            "model_type": {"type": "string", "enum": ["text", "image", "embedding", "decision"]},
            "description": {"type": "string"},
            "custom_endpoint_path": {"type": "string", "description": "Endpoint path for custom-format providers"},
            "input_cost_per_1k": {"type": "number"},
            "output_cost_per_1k": {"type": "number"},
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
    input_cost_per_1k: float | None = None,
    output_cost_per_1k: float | None = None,
    max_tokens: int | None = None,
    default_temperature: float | None = None,
    is_active: bool | None = None,
    is_default: bool | None = None,
) -> dict[str, Any]:
    """Partially update an LLM model. Requires confirmation."""
    model = await LLMModel.objects.get(id=model_id)
    if not model:
        return {"error": "model_not_found", "message": f"Model {model_id} not found"}

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
    if input_cost_per_1k is not None:
        updates["input_cost_per_1k"] = input_cost_per_1k
    if output_cost_per_1k is not None:
        updates["output_cost_per_1k"] = output_cost_per_1k
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
    }


@tool(
    name="llm_model_delete",
    description="Delete an LLM model with default reassignment preview (requires confirmation)",
    confirm=True,
    required_permission="llmmodel.delete",
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
