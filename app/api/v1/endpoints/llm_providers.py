"""
API endpoints for LLM Provider management.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import require_model_perm
from app.models import LLMProvider
from app.schemas.llm_provider import LLMProviderCreate, LLMProviderList, LLMProviderResponse, LLMProviderUpdate
from app.types import ActionType

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/llm-providers", tags=["LLM Providers"])


def _provider_response(p: LLMProvider) -> LLMProviderResponse:
    return LLMProviderResponse(
        id=p.id,
        uid=p.uid,
        owner_user_id=p.owner_user_id,
        name=p.name,
        description=p.description,
        api_format=p.api_format,
        base_url=p.base_url,
        auth_header=None,
        is_active=p.is_active,
        created_at=p.created_at.isoformat() if p.created_at else "",
        updated_at=p.updated_at.isoformat() if p.updated_at else "",
    )


@router.post("/", response_model=LLMProviderResponse, status_code=status.HTTP_201_CREATED)
async def create_llm_provider(
    request: LLMProviderCreate,
    _user=Depends(require_model_perm("llmprovider", ActionType.CREATE)),
):
    """Create a new LLM provider."""
    try:
        data = request.model_dump(exclude_unset=True)
        from app.services.ai.personal_catalog import save_provider_bundle

        result = await save_provider_bundle(user_id=_user.id, provider=data, models=[])
        provider = await LLMProvider.objects.get(id=result["id"])
        logger.info(f"LLM provider created: {provider.name} (ID: {provider.id})")
        return _provider_response(provider)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Personal catalog access denied")
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid personal catalog data")
    except Exception as e:
        logger.error("Personal catalog operation failed")
        raise HTTPException(status_code=500, detail="Personal catalog operation failed")


@router.get("/", response_model=LLMProviderList)
async def list_llm_providers(
    is_active: bool = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _user=Depends(require_model_perm("llmprovider", ActionType.VIEW)),
):
    """List all LLM providers."""
    try:
        query = LLMProvider.objects.filter(is_active=is_active) if is_active is not None else LLMProvider.objects.all()
        total = await query.count()
        providers = await query.order_by("name", "id").limit(limit).offset(offset)
        return LLMProviderList(providers=[_provider_response(p) for p in providers], total=total)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Personal catalog access denied")
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid personal catalog data")
    except Exception as e:
        logger.error("Personal catalog operation failed")
        raise HTTPException(status_code=500, detail="Personal catalog operation failed")


@router.get("/{provider_id}", response_model=LLMProviderResponse)
async def get_llm_provider(
    provider_id: int,
    _user=Depends(require_model_perm("llmprovider", ActionType.VIEW)),
):
    """Get a specific LLM provider by ID."""
    try:
        provider = await LLMProvider.objects.get(id=provider_id)
        if not provider:
            raise HTTPException(status_code=404, detail=f"LLM provider {provider_id} not found")
        return _provider_response(provider)
    except HTTPException:
        raise
    except PermissionError:
        raise HTTPException(status_code=403, detail="Personal catalog access denied")
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid personal catalog data")
    except Exception as e:
        logger.error("Personal catalog operation failed")
        raise HTTPException(status_code=500, detail="Personal catalog operation failed")


@router.patch("/{provider_id}", response_model=LLMProviderResponse)
async def update_llm_provider(
    provider_id: int,
    request: LLMProviderUpdate,
    _user=Depends(require_model_perm("llmprovider", ActionType.UPDATE)),
):
    """Update an LLM provider."""
    try:
        provider = await LLMProvider.objects.get(id=provider_id)
        if not provider:
            raise HTTPException(status_code=404, detail=f"LLM provider {provider_id} not found")
        updates = request.model_dump(exclude_unset=True)
        from app.services.ai.personal_catalog import save_provider_bundle

        await save_provider_bundle(user_id=_user.id, provider={"id": provider_id, **updates}, models=[])
        provider = await LLMProvider.objects.get(id=provider_id)
        logger.info(f"LLM provider updated: {provider.name}")
        return _provider_response(provider)
    except HTTPException:
        raise
    except PermissionError:
        raise HTTPException(status_code=403, detail="Personal catalog access denied")
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid personal catalog data")
    except Exception as e:
        logger.error("Personal catalog operation failed")
        raise HTTPException(status_code=500, detail="Personal catalog operation failed")


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_llm_provider(
    provider_id: int,
    _user=Depends(require_model_perm("llmprovider", ActionType.DELETE)),
):
    """Delete an LLM provider."""
    try:
        provider = await LLMProvider.objects.get(id=provider_id)
        if not provider:
            raise HTTPException(status_code=404, detail=f"LLM provider {provider_id} not found")
        await LLMProvider.objects.delete_by_id(provider_id)
        logger.info(f"LLM provider deleted: {provider.name}")
    except HTTPException:
        raise
    except PermissionError:
        raise HTTPException(status_code=403, detail="Personal catalog access denied")
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid personal catalog data")
    except Exception as e:
        logger.error("Personal catalog operation failed")
        raise HTTPException(status_code=500, detail="Personal catalog operation failed")
