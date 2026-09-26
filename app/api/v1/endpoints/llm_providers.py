"""
API endpoints for LLM Provider management.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.models import LLMProvider, User
from app.schemas.llm_provider import LLMProviderCreate, LLMProviderList, LLMProviderResponse, LLMProviderUpdate
from app.services.user.auth import get_authenticated_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/llm-providers", tags=["LLM Providers"])


def _provider_response(p: LLMProvider) -> LLMProviderResponse:
    return LLMProviderResponse(
        id=p.id, name=p.name, description=p.description,
        api_format=p.api_format, base_url=p.base_url, auth_header=p.auth_header,
        is_active=p.is_active, is_default=p.is_default,
        created_at=p.created_at.isoformat() if p.created_at else "",
        updated_at=p.updated_at.isoformat() if p.updated_at else "",
    )


@router.post("/", response_model=LLMProviderResponse, status_code=status.HTTP_201_CREATED)
async def create_llm_provider(request: LLMProviderCreate, current_user: User = Depends(get_authenticated_user)):
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        data = request.model_dump()
        provider = await LLMProvider.objects.create(**data)
        logger.info(f"LLM provider created: {provider.name} (ID: {provider.id})")
        return _provider_response(provider)
    except Exception as e:
        logger.error(f"Error creating LLM provider: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create LLM provider: {e}")


@router.get("/", response_model=LLMProviderList)
async def list_llm_providers(is_active: bool = None, current_user: User = Depends(get_authenticated_user)):
    try:
        providers = await LLMProvider.objects.filter(is_active=is_active) if is_active is not None else await LLMProvider.objects.all()
        return LLMProviderList(providers=[_provider_response(p) for p in providers], total=len(providers))
    except Exception as e:
        logger.error(f"Error listing LLM providers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list LLM providers: {e}")


@router.get("/{provider_id}", response_model=LLMProviderResponse)
async def get_llm_provider(provider_id: int, current_user: User = Depends(get_authenticated_user)):
    try:
        provider = await LLMProvider.objects.get(id=provider_id)
        if not provider:
            raise HTTPException(status_code=404, detail=f"LLM provider {provider_id} not found")
        return _provider_response(provider)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting LLM provider {provider_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get LLM provider: {e}")


@router.patch("/{provider_id}", response_model=LLMProviderResponse)
async def update_llm_provider(provider_id: int, request: LLMProviderUpdate,
                              current_user: User = Depends(get_authenticated_user)):
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        provider = await LLMProvider.objects.get(id=provider_id)
        if not provider:
            raise HTTPException(status_code=404, detail=f"LLM provider {provider_id} not found")
        updates = request.model_dump(exclude_unset=True)
        for key, value in updates.items():
            setattr(provider, key, value)
        await LLMProvider.objects.update_by_id(provider_id, **updates)
        provider = await LLMProvider.objects.get(id=provider_id)
        logger.info(f"LLM provider updated: {provider.name}")
        return _provider_response(provider)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating LLM provider {provider_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update LLM provider: {e}")


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_llm_provider(provider_id: int, current_user: User = Depends(get_authenticated_user)):
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        provider = await LLMProvider.objects.get(id=provider_id)
        if not provider:
            raise HTTPException(status_code=404, detail=f"LLM provider {provider_id} not found")
        await LLMProvider.objects.delete(provider_id)
        logger.info(f"LLM provider deleted: {provider.name}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting LLM provider {provider_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete LLM provider: {e}")