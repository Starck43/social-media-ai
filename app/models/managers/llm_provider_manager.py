import logging
from typing import Optional, List, TYPE_CHECKING

from .base_manager import BaseManager

if TYPE_CHECKING:
	from ..llm_provider import LLMProvider

logger = logging.getLogger(__name__)


class LLMProviderManager(BaseManager):
	"""Manager for LLMProvider model operations."""

	def __init__(self):
		from ..llm_provider import LLMProvider
		super().__init__(LLMProvider)

	async def get_by_capability(self, capability: str, is_active: bool = True) -> list['LLMProvider']:
		"""
		Get all LLM providers that support a specific capability.
		
		Args:
			capability: Capability to filter by (text, image, video)
			is_active: Filter by active status
			
		Returns:
			List of LLMProvider objects
		"""
		query = self.model.objects.select()
		
		if is_active:
			query = query.where(self.model.is_active == True)
		
		# Filter by capability in JSON array
		query = query.where(self.model.capabilities.contains([capability]))
		
		providers = await query
		return list(providers)

	async def get_active_providers(self) -> List:
		"""Get all active LLM providers."""
		return await self.filter(is_active=True)

	async def get_by_type(self, provider_type: str, is_active: bool = True) -> List:
		"""
		Get all LLM providers by type.
		
		Args:
			provider_type: Provider type (deepseek, openai, etc.)
			is_active: Filter by active status
			
		Returns:
			List of LLMProvider objects
		"""
		filters = {"provider_type": provider_type}
		if is_active:
			filters["is_active"] = True
		
		return await self.filter(**filters)

	async def get_default_for_media_type(self, media_type: str) -> Optional:
		"""
		Get the default LLM provider for a specific media type.
		Selects the first active provider that supports the capability.
		
		Args:
			media_type: Media type (text, image, video)
			
		Returns:
			LLMProvider object or None
		"""
		providers = await self.get_by_capability(media_type, is_active=True)
		
		if providers:
			logger.info(f"Using default LLM provider for {media_type}: {providers[0].name}")
			return providers[0]
		
		logger.warning(f"No active LLM provider found for {media_type}")
		return None
