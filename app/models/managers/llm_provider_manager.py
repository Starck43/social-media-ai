import logging
from typing import TYPE_CHECKING

from .base_manager import BaseManager

if TYPE_CHECKING:
	from ..llm_provider import LLMProvider

logger = logging.getLogger(__name__)


class LLMProviderManager(BaseManager):
	"""Manager for LLMProvider model operations."""

	def __init__(self):
		from ..llm_provider import LLMProvider
		super().__init__(LLMProvider)

	async def _get_by_capability(self, capability: str, is_active: bool = True) -> list['LLMProvider']:
		"""
		Get all LLM providers that support a specific capability.

		Args:
			capability: Capability to filter by (text, image, video)
			is_active: Filter by active status

		Returns:
			List of LLMProvider objects
		"""
		from ..llm_model import LLMModel

		# Get all active providers
		if is_active:
			providers = await self.filter(is_active=True)
		else:
			providers = await self.all()

		# Get all active models
		models = await LLMModel.objects.filter(is_active=True)

		# Find providers that have models with the required capability
		providers_with_capability = []
		for provider in providers:
			# Check if provider has any active models with this capability
			provider_models = [m for m in models if m.provider_id == provider.id and capability in m.capabilities]
			if provider_models:
				providers_with_capability.append(provider)

		return providers_with_capability

	async def get_active_providers(self) -> list['LLMProvider']:
		"""Get all active LLM providers."""
		return await self.filter(is_active=True)


