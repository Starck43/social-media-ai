"""Metadata utilities for LLM providers."""
import logging
from typing import Dict, Any, Optional

from app.core.llm_presets import LLMProviderMetadata
from app.types import LLMProviderType
from app.utils.enum_helpers import get_enum_value

logger = logging.getLogger(__name__)


class LLMMetadataHelper:
	"""Helper class for working with LLM provider metadata."""
	
	@staticmethod
	def get_provider_config(provider_type: str) -> Optional[dict[str, Any]]:
		"""
		Get provider configuration by type.
		
		Args:
			provider_type: Provider type string (deepseek, openai, etc.)
			
		Returns:
			Provider configuration dict or None
		"""
		try:
			return LLMProviderMetadata.get_provider_config(provider_type)
		except Exception as e:
			logger.warning(f"Failed to get config for {provider_type}: {e}")
			return None
	
	@staticmethod
	def get_model_info(provider_type: str, model_id: str) -> Optional[Any]:
		"""
		Get model information.
		
		Args:
			provider_type: Provider type string
			model_id: Model identifier
			
		Returns:
			Model info object or None
		"""
		try:
			return LLMProviderMetadata.get_model_info(provider_type, model_id)
		except Exception as e:
			logger.warning(f"Failed to get model info for {provider_type}/{model_id}: {e}")
			return None
	
	@staticmethod
	def get_metadata_for_js() -> dict[str, Any]:
		"""
		Get all provider metadata formatted for JavaScript injection.
		
		Returns:
			Dictionary with provider metadata for JS autofill
		"""
		metadata = {}
		
		for provider_type in LLMProviderType:
			provider_value = provider_type.value
			config = LLMMetadataHelper.get_provider_config(provider_value)
			
			if config:
				# Get available models (dict of model_id -> ModelInfo)
				models = []
				available_models = provider_type.available_models
				
				# Iterate over ModelInfo objects
				for model_info in available_models.values():
					models.append({
						'id': model_info.model_id,
						'name': model_info.name,
						'capabilities': [get_enum_value(cap) for cap in model_info.capabilities],
					})
				
				metadata[provider_value] = {
					'api_url': config.get('api_url'),
					'api_key_env': config.get('api_key_env'),
					'display_name': config.get('display_name'),
					'models': models,
				}
		
		return metadata
	
	@staticmethod
	def validate_provider_config(provider_type: str, model_id: str, capabilities: list) -> tuple[bool, Optional[str]]:
		"""
		Validate provider configuration.
		
		Args:
			provider_type: Provider type
			model_id: Model identifier
			capabilities: List of capabilities
			
		Returns:
			Tuple of (is_valid, error_message)
		"""
		# Check if provider exists
		config = LLMMetadataHelper.get_provider_config(provider_type)
		if not config:
			return False, f"Unknown provider type: {provider_type}"
		
		# Check if model exists
		model_info = LLMMetadataHelper.get_model_info(provider_type, model_id)
		if not model_info:
			return False, f"Model {model_id} not found for provider {provider_type}"
		
		# Check if capabilities match model
		model_capabilities = set(model_info.capabilities)
		requested_capabilities = set(capabilities)
		
		unsupported = requested_capabilities - model_capabilities
		if unsupported:
			return False, f"Model {model_id} doesn't support: {', '.join(unsupported)}"
		
		return True, None
