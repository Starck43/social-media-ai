import logging
from typing import Optional, TYPE_CHECKING

from .base_manager import BaseManager

if TYPE_CHECKING:
	from ..llm_model import LLMModel

logger = logging.getLogger(__name__)


class LLMModelManager(BaseManager):
	"""Manager for LLMModel operations."""

	def __init__(self):
		from ..llm_model import LLMModel
		super().__init__(LLMModel)

	async def get_active_models(self) -> list['LLMModel']:
		"""Get all active LLM models."""
		return await self.filter(is_active=True)

	async def get_models_by_provider(self, provider_id: int, is_active: bool = True) -> list['LLMModel']:
		"""
		Get all models for a specific provider.

		Args:
			provider_id: ID of the LLM provider
			is_active: Filter by active status

		Returns:
			List of LLMModel objects
		"""
		filters = {"provider_id": provider_id}
		if is_active:
			filters["is_active"] = True

		return await self.filter(**filters)

	async def get_model_for_capability(self, capability: str, provider_id: int = None) -> Optional['LLMModel']:
		"""
		Get the best model for a specific capability.

		Args:
			capability: Capability to filter by (text, image, video)
			provider_id: Optional provider ID to limit search

		Returns:
			LLMModel object or None
		"""
		if provider_id:
			# Существующая логика с provider_id
			models = await self.get_models_by_provider(provider_id=provider_id)

			# Filter by capability and return first match
			for model in models:
				if model.can_handle(capability):
					logger.info(f"Using model for {capability}: {model.name}")
					return model
		else:
			return await self._get_default_for_capability(capability)

		logger.warning(f"No active model found for {capability}" + (f" in provider {provider_id}" if provider_id else ""))
		return None

	async def _get_by_capability(self, capability: str, is_active: bool = True) -> list['LLMModel']:
		"""
		Get all LLM models that support a specific capability.

		Args:
			capability: Capability to filter by (text, image, video)
			is_active: Filter by active status

		Returns:
			List of LLMModel objects
		"""
		if is_active:
			all_models = await self.filter(is_active=True)
		else:
			all_models = await self.all()

		# Filter by capability in Python (since JSON contains is problematic)
		models = [
			m for m in all_models
			if m.capabilities and capability in m.capabilities
		]
		return models

	async def _get_default_for_capability(self, capability: str) -> Optional['LLMModel']:
		"""
		Get default model for a specific capability (without provider restriction).

		Args:
			capability: Capability to filter by (text, image, video)

		Returns:
			LLMModel object or None
		"""
		default_models = await self._get_by_capability(capability, is_active=True)
		default_models = [m for m in default_models if m.is_default]

		if default_models:
			logger.info(f"Found default model for {capability}: {default_models[0].name}")
			return default_models[0]

		fallback_models = await self._get_by_capability(capability, is_active=True)
		if fallback_models:
			logger.info(f"Using fallback model for {capability}: {fallback_models[0].name}")
			return fallback_models[0]

		logger.warning(f"No active LLM model found for {capability}")
		return None

