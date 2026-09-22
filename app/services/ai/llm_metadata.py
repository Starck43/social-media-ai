"""Metadata utilities for LLM providers."""
import logging
from typing import Any, Optional

from app.core.config import settings
from app.models import LLMProvider, LLMModel

logger = logging.getLogger(__name__)


class LLMMetadataHelper:
	"""Helper class for working with LLM provider metadata."""

	@staticmethod
	async def get_provider_config(provider_type: str) -> Optional[dict[str, Any]]:
		"""
		Get provider configuration by type.

		Args:
			provider_type: Provider type string (deepseek, openai, etc.)

		Returns:
			Provider configuration dict or None
		"""
		try:
			# Find provider by type
			provider = await LLMProvider.objects.filter(
				provider_type=provider_type,
				is_active=True
			).first()

			if not provider:
				return None

			# Get models for this provider
			models = await LLMMetadataHelper.get_provider_models(provider.id)

			return {
				"display_name": provider.name,
				"api_url": provider.api_url,
				"api_key_env": provider.api_key_env,
				"models": models,
			}
		except Exception as e:
			logger.warning(f"Failed to get config for {provider_type}: {e}")
			return None

	@staticmethod
	async def get_provider_models(provider_id: int) -> dict[str, Any]:
		"""Get models for a provider from database."""
		try:
			models = await LLMModel.objects.filter(
				provider_id=provider_id,
				is_active=True
			).all()

			result = {}
			for model in models:
				result[model.name] = {
					'id': model.name,
					'name': model.name,
					'capabilities': model.capabilities,
					'input_cost': model.input_cost,
					'output_cost': model.output_cost,
					'context_window': model.config.get("max_tokens", settings.LLM_DEFAULT_MAX_TOKENS),
				}
			return result
		except Exception as e:
			logger.warning(f"Failed to get models for provider {provider_id}: {e}")
			return {}

	@staticmethod
	async def get_model_info(provider_type: str, model_id: str) -> Optional[Any]:
		"""
		Get model information.

		Args:
			provider_type: Provider type string
			model_id: Model identifier

		Returns:
			Model info object or None
		"""
		try:
			# Find provider
			provider = await LLMProvider.objects.filter(
				provider_type=provider_type,
				is_active=True
			).first()

			if not provider:
				return None

			# Find model
			model = await LLMModel.objects.filter(
				provider_id=provider.id,
				name=model_id,
				is_active=True
			).first()

			if not model:
				return None

			return {
				'id': model.name,
				'name': model.name,
				'capabilities': model.capabilities,
				'input_cost': model.input_cost,
				'output_cost': model.output_cost,
				'context_window': model.model.config.get("max_tokens", settings.LLM_DEFAULT_MAX_TOKENS),
			}
		except Exception as e:
			logger.warning(f"Failed to get model info for {provider_type}/{model_id}: {e}")
			return None

	@staticmethod
	async def get_metadata_for_js() -> dict[str, Any]:
		"""
		Get all provider metadata formatted for JavaScript injection.

		Returns:
			Dictionary with provider metadata for JS autofill
		"""
		metadata = {}

		try:
			# Get all active providers
			providers = await LLMProvider.objects.filter(is_active=True).all()

			for provider in providers:
				config = await LLMMetadataHelper.get_provider_config(provider.provider_type)

				if config:
					# Get available models
					models = []
					provider_models = await LLMMetadataHelper.get_provider_models(provider.id)

					for model_info in provider_models.values():
						models.append({
							'id': model_info['id'],
							'name': model_info['name'],
							'capabilities': model_info['capabilities'],
						})

					metadata[provider.provider_type] = {
						'api_url': config.get('api_url'),
						'api_key_env': config.get('api_key_env'),
						'display_name': config.get('display_name'),
						'models': models,
					}

		except Exception as e:
			logger.warning(f"Failed to get metadata for JS: {e}")

		return metadata

	@staticmethod
	async def validate_provider_config(provider_type: str, model_id: str, capabilities: list) -> tuple[bool, Optional[str]]:
		"""
		Validate provider configuration.

		Args:
			provider_type: Provider type
			model_id: Model identifier
			capabilities: List of capabilities

		Returns:
			Tuple of (is_valid, error_message)
		"""
		try:
			# Check if provider exists
			provider = await LLMProvider.objects.filter(
				provider_type=provider_type,
				is_active=True
			).first()

			if not provider:
				return False, f"Unknown provider type: {provider_type}"

			# Check if model exists
			model_info = await LLMMetadataHelper.get_model_info(provider_type, model_id)
			if not model_info:
				return False, f"Model {model_id} not found for provider {provider_type}"

			# Check if capabilities match model
			model_capabilities = set(model_info['capabilities'])
			requested_capabilities = set(capabilities)

			unsupported = requested_capabilities - model_capabilities
			if unsupported:
				return False, f"Model {model_id} doesn't support: {', '.join(unsupported)}"

			return True, None
		except Exception as e:
			logger.warning(f"Failed to validate provider config: {e}")
			return False, f"Validation error: {e}"
