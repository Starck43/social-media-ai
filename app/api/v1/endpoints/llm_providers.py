"""
API endpoints for LLM Provider management.
"""

import logging

from fastapi import APIRouter, HTTPException, Depends, status

from app.models import LLMProvider, User
from app.schemas.llm_provider import (
	LLMProviderCreate,
	LLMProviderUpdate,
	LLMProviderResponse,
	LLMProviderList
)
from app.services.user.auth import get_authenticated_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/llm-providers", tags=["LLM Providers"])


@router.post("/", response_model=LLMProviderResponse, status_code=status.HTTP_201_CREATED)
async def create_llm_provider(
	request: LLMProviderCreate,
	current_user: User = Depends(get_authenticated_user)
):
	"""
	Create a new LLM provider configuration.
	
	Requires: Admin access
	"""
	if not current_user.is_superuser:
		raise HTTPException(status_code=403, detail="Admin access required")
	try:
		provider = await LLMProvider.objects.create(
			name=request.name,
			description=request.description,
			provider_type=request.provider_type,
			api_url=request.api_url,
			api_key_env=request.api_key_env,
			model_name=request.model_name,
			capabilities=request.capabilities,
			config=request.config,
			is_active=request.is_active
		)
		
		logger.info(f"LLM provider created: {provider.name} (ID: {provider.id}) by user {current_user.username}")
		
		return LLMProviderResponse(
			id=provider.id,
			name=provider.name,
			description=provider.description,
			provider_type=provider.provider_type.value if hasattr(provider.provider_type, 'value') else str(provider.provider_type),
			api_url=provider.api_url,
			api_key_env=provider.api_key_env,
			model_name=provider.model_name,
			capabilities=provider.capabilities,
			config=provider.config or {},
			is_active=provider.is_active,
			created_at=provider.created_at.isoformat() if provider.created_at else "",
			updated_at=provider.updated_at.isoformat() if provider.updated_at else ""
		)
	
	except Exception as e:
		logger.error(f"Error creating LLM provider: {e}", exc_info=True)
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail=f"Failed to create LLM provider: {str(e)}"
		)


@router.get("/", response_model=LLMProviderList)
async def list_llm_providers(
	is_active: bool = None,
	capability: str = None,
	current_user: User = Depends(get_authenticated_user)
):
	"""
	List all LLM providers with optional filtering.
	
	Requires: Authenticated user
	
	Query params:
	— is_active: Filter by active status
	— capability: Filter by capability (text, image, video)
	"""
	try:
		if capability:
			providers = await LLMProvider.objects.get_by_capability(capability, is_active=is_active if is_active is not None else True)
		elif is_active is not None:
			providers = await LLMProvider.objects.filter(is_active=is_active)
		else:
			providers = await LLMProvider.objects.all()
		
		response_providers = []
		for provider in providers:
			response_providers.append(LLMProviderResponse(
				id=provider.id,
				name=provider.name,
				description=provider.description,
				provider_type=provider.provider_type.value if hasattr(provider.provider_type, 'value') else str(provider.provider_type),
				api_url=provider.api_url,
				api_key_env=provider.api_key_env,
				model_name=provider.model_name,
				capabilities=provider.capabilities,
				config=provider.config or {},
				is_active=provider.is_active,
				created_at=provider.created_at.isoformat() if provider.created_at else "",
				updated_at=provider.updated_at.isoformat() if provider.updated_at else ""
			))
		
		return LLMProviderList(providers=response_providers, total=len(response_providers))
	
	except Exception as e:
		logger.error(f"Error listing LLM providers: {e}", exc_info=True)
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail=f"Failed to list LLM providers: {str(e)}"
		)


@router.get("/{provider_id}", response_model=LLMProviderResponse)
async def get_llm_provider(
	provider_id: int,
	current_user: User = Depends(get_authenticated_user)
):
	"""
	Get details of a specific LLM provider.
	
	Requires: Authenticated user
	"""
	try:
		provider = await LLMProvider.objects.get(id=provider_id)
		
		if not provider:
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail=f"LLM provider {provider_id} not found"
			)
		
		return LLMProviderResponse(
			id=provider.id,
			name=provider.name,
			description=provider.description,
			provider_type=provider.provider_type.value if hasattr(provider.provider_type, 'value') else str(provider.provider_type),
			api_url=provider.api_url,
			api_key_env=provider.api_key_env,
			model_name=provider.model_name,
			capabilities=provider.capabilities,
			config=provider.config or {},
			is_active=provider.is_active,
			created_at=provider.created_at.isoformat() if provider.created_at else "",
			updated_at=provider.updated_at.isoformat() if provider.updated_at else ""
		)
	
	except HTTPException:
		raise
	except Exception as e:
		logger.error(f"Error getting LLM provider {provider_id}: {e}", exc_info=True)
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail=f"Failed to get LLM provider: {str(e)}"
		)


@router.patch("/{provider_id}", response_model=LLMProviderResponse)
async def update_llm_provider(
	provider_id: int,
	request: LLMProviderUpdate,
	current_user: User = Depends(get_authenticated_user)
):
	"""
	Update an LLM provider configuration.
	
	Requires: Admin access
	"""
	if not current_user.is_superuser:
		raise HTTPException(status_code=403, detail="Admin access required")
	try:
		provider = await LLMProvider.objects.get(id=provider_id)
		
		if not provider:
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail=f"LLM provider {provider_id} not found"
			)
		
		# Update fields
		updates = request.model_dump(exclude_unset=True)
		for key, value in updates.items():
			setattr(provider, key, value)
		
		await LLMProvider.objects.update_by_id(provider_id, **updates)
		
		# Reload provider
		provider = await LLMProvider.objects.get(id=provider_id)
		
		logger.info(f"LLM provider updated: {provider.name} (ID: {provider.id}) by user {current_user.username}")
		
		return LLMProviderResponse(
			id=provider.id,
			name=provider.name,
			description=provider.description,
			provider_type=provider.provider_type.value if hasattr(provider.provider_type, 'value') else str(provider.provider_type),
			api_url=provider.api_url,
			api_key_env=provider.api_key_env,
			model_name=provider.model_name,
			capabilities=provider.capabilities,
			config=provider.config or {},
			is_active=provider.is_active,
			created_at=provider.created_at.isoformat() if provider.created_at else "",
			updated_at=provider.updated_at.isoformat() if provider.updated_at else ""
		)
	
	except HTTPException:
		raise
	except Exception as e:
		logger.error(f"Error updating LLM provider {provider_id}: {e}", exc_info=True)
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail=f"Failed to update LLM provider: {str(e)}"
		)


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_llm_provider(
	provider_id: int,
	current_user: User = Depends(get_authenticated_user)
):
	"""
	Delete an LLM provider.
	
	Requires: Admin access
	"""
	if not current_user.is_superuser:
		raise HTTPException(status_code=403, detail="Admin access required")
	try:
		provider = await LLMProvider.objects.get(id=provider_id)
		
		if not provider:
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail=f"LLM provider {provider_id} not found"
			)
		
		await LLMProvider.objects.delete(provider_id)
		
		logger.info(f"LLM provider deleted: {provider.name} (ID: {provider.id}) by user {current_user.username}")
	
	except HTTPException:
		raise
	except Exception as e:
		logger.error(f"Error deleting LLM provider {provider_id}: {e}", exc_info=True)
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail=f"Failed to delete LLM provider: {str(e)}"
		)
