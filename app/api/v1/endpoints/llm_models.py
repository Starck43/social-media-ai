"""
API endpoints for LLM Model management.
"""
import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import require_model_perm
from app.models import LLMModel
from app.schemas.llm_model import LLMModelCreate, LLMModelList, LLMModelResponse, LLMModelUpdate
from app.types import ActionType

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/llm-models", tags=["LLM Models"])


def _model_response(m: LLMModel) -> LLMModelResponse:
    unit = m.pricing_token_unit or 1000
    input_raw = Decimal(str(m.input_cost_per_1k))
    output_raw = Decimal(str(m.output_cost_per_1k))
    return LLMModelResponse(
        id=m.id,
        name=m.name,
        model_id=m.model_id,
        description=m.description,
        provider_id=m.provider_id,
        model_type=m.model_type,
        custom_endpoint_path=m.custom_endpoint_path,
        pricing_token_unit=unit,
        input_cost_per_1k=input_raw * Decimal(1000) / Decimal(unit),
        output_cost_per_1k=output_raw * Decimal(1000) / Decimal(unit),
        input_cost_per_1m=input_raw * Decimal(1_000_000) / Decimal(unit),
        output_cost_per_1m=output_raw * Decimal(1_000_000) / Decimal(unit),
        cache_read_cost_per_1m=m.cache_read_cost_per_1m,
        cache_write_5m_cost_per_1m=m.cache_write_5m_cost_per_1m,
        cache_write_1h_cost_per_1m=m.cache_write_1h_cost_per_1m,
        max_tokens=m.max_tokens,
        default_temperature=m.default_temperature,
        is_active=m.is_active,
        is_default=m.is_default,
        last_used_at=m.last_used_at.isoformat() if m.last_used_at else None,
        last_success_at=m.last_success_at.isoformat() if m.last_success_at else None,
        last_error_at=m.last_error_at.isoformat() if m.last_error_at else None,
        use_count=m.use_count,
        fail_count=m.fail_count,
        created_at=m.created_at.isoformat() if m.created_at else "",
        updated_at=m.updated_at.isoformat() if m.updated_at else "",
    )


def _raw_price_updates(data: dict, unit: int, *, create: bool = False) -> dict:
    """Translate public per-1K/per-1M rates into the catalog's selected raw unit."""
    updates = {}
    for field in ("input", "output"):
        legacy_key = f"{field}_cost_per_1k"
        canonical_key = f"{field}_cost_per_1m"
        legacy = data.pop(legacy_key, None)
        canonical = data.pop(canonical_key, None)
        if legacy is not None:
            canonical = legacy * Decimal(1000)
        if canonical is not None:
            updates[legacy_key] = canonical * Decimal(unit) / Decimal(1_000_000)
        elif create:
            updates[legacy_key] = 0.0
    return updates


@router.post("/", response_model=LLMModelResponse, status_code=status.HTTP_201_CREATED)
async def create_llm_model(
    request: LLMModelCreate,
    _user = Depends(require_model_perm("llmmodel", ActionType.CREATE)),
):
    """Create a new LLM model (default-uniqueness enforced by the manager)."""
    try:
        data = request.model_dump(exclude_unset=True)
        unit = data.pop("pricing_token_unit", 1_000_000)
        prices = _raw_price_updates(data, unit, create=True)
        model = await LLMModel.objects.create_model(**data, **prices, pricing_token_unit=unit)
        logger.info(f"LLM model created: {model.name} (ID: {model.id})")
        return _model_response(model)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Personal catalog access denied")
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid personal catalog data")
    except Exception as e:
        logger.error("Personal catalog operation failed")
        raise HTTPException(status_code=500, detail="Personal catalog operation failed")


@router.get("/", response_model=LLMModelList)
async def list_llm_models(
    is_active: bool = None,
    provider_id: int = None,
    model_type: str = None,
    _user = Depends(require_model_perm("llmmodel", ActionType.VIEW)),
):
    """List all LLM models with optional filters."""
    try:
        filters = {}
        if is_active is not None:
            filters["is_active"] = is_active
        if provider_id is not None:
            filters["provider_id"] = provider_id
        if model_type is not None:
            filters["model_type"] = model_type

        models = await LLMModel.objects.select_related("provider").filter(**filters).order_by("name")
        return LLMModelList(models=[_model_response(m) for m in models], total=len(models))
    except PermissionError:
        raise HTTPException(status_code=403, detail="Personal catalog access denied")
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid personal catalog data")
    except Exception as e:
        logger.error("Personal catalog operation failed")
        raise HTTPException(status_code=500, detail="Personal catalog operation failed")


@router.get("/{model_id}", response_model=LLMModelResponse)
async def get_llm_model(
    model_id: int,
    _user = Depends(require_model_perm("llmmodel", ActionType.VIEW)),
):
    """Get a specific LLM model by ID."""
    try:
        model = await LLMModel.objects.select_related("provider").get(id=model_id)
        if not model:
            raise HTTPException(status_code=404, detail=f"LLM model {model_id} not found")
        return _model_response(model)
    except HTTPException:
        raise
    except PermissionError:
        raise HTTPException(status_code=403, detail="Personal catalog access denied")
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid personal catalog data")
    except Exception as e:
        logger.error("Personal catalog operation failed")
        raise HTTPException(status_code=500, detail="Personal catalog operation failed")


@router.patch("/{model_id}", response_model=LLMModelResponse)
async def update_llm_model(
    model_id: int,
    request: LLMModelUpdate,
    _user = Depends(require_model_perm("llmmodel", ActionType.UPDATE)),
):
    """Update an LLM model (default-uniqueness enforced by the manager)."""
    try:
        updates = request.model_dump(exclude_unset=True)
        if not updates:
            model = await LLMModel.objects.select_related("provider").get(id=model_id)
            if not model:
                raise HTTPException(status_code=404, detail=f"LLM model {model_id} not found")
            return _model_response(model)
        existing = await LLMModel.objects.get(id=model_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"LLM model {model_id} not found")
        unit = updates.get("pricing_token_unit", existing.pricing_token_unit)
        prices = _raw_price_updates(updates, unit)
        updates.update(prices)
        model = await LLMModel.objects.update_model(model_id, **updates)
        if not model:
            raise HTTPException(status_code=404, detail=f"LLM model {model_id} not found")
        logger.info(f"LLM model updated: {model.name}")
        return _model_response(model)
    except HTTPException:
        raise
    except PermissionError:
        raise HTTPException(status_code=403, detail="Personal catalog access denied")
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid personal catalog data")
    except Exception as e:
        logger.error("Personal catalog operation failed")
        raise HTTPException(status_code=500, detail="Personal catalog operation failed")


@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_llm_model(
    model_id: int,
    _user = Depends(require_model_perm("llmmodel", ActionType.DELETE)),
):
    """Delete an LLM model with default reassignment."""
    try:
        # Use manager's reassignment logic (same as admin/agent)
        result = await LLMModel.objects.delete_with_default_reassignment(model_id)
        logger.info(f"LLM model deleted: {model_id}, reassignment: {result}")
    except HTTPException:
        raise
    except PermissionError:
        raise HTTPException(status_code=403, detail="Personal catalog access denied")
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid personal catalog data")
    except Exception as e:
        logger.error("Personal catalog operation failed")
        raise HTTPException(status_code=500, detail="Personal catalog operation failed")
