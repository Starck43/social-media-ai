"""
API endpoints for LLM Model management.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import require_model_perm
from app.models import LLMModel
from app.schemas.llm_model import LLMModelCreate, LLMModelList, LLMModelResponse, LLMModelUpdate
from app.types import ActionType

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/llm-models", tags=["LLM Models"])


def _model_response(m: LLMModel) -> LLMModelResponse:
    return LLMModelResponse(
        id=m.id,
        name=m.name,
        model_id=m.model_id,
        description=m.description,
        provider_id=m.provider_id,
        model_type=m.model_type,
        input_cost_per_1k=m.input_cost_per_1k,
        output_cost_per_1k=m.output_cost_per_1k,
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


@router.post("/", response_model=LLMModelResponse, status_code=status.HTTP_201_CREATED)
async def create_llm_model(
    request: LLMModelCreate,
    _user = Depends(require_model_perm("llmmodel", ActionType.CREATE)),
):
    """Create a new LLM model (default-uniqueness enforced by the manager)."""
    try:
        model = await LLMModel.objects.create_model(**request.model_dump())
        logger.info(f"LLM model created: {model.name} (ID: {model.id})")
        return _model_response(model)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating LLM model: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create LLM model: {e}")


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
    except Exception as e:
        logger.error(f"Error listing LLM models: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list LLM models: {e}")


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
    except Exception as e:
        logger.error(f"Error getting LLM model {model_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get LLM model: {e}")


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
        model = await LLMModel.objects.update_model(model_id, **updates)
        if not model:
            raise HTTPException(status_code=404, detail=f"LLM model {model_id} not found")
        logger.info(f"LLM model updated: {model.name}")
        return _model_response(model)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating LLM model {model_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update LLM model: {e}")


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
    except Exception as e:
        logger.error(f"Error deleting LLM model {model_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete LLM model: {e}")