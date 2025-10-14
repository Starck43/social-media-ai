"""LLM provider enum types."""
from enum import Enum

from app.utils.db_enums import database_enum

@database_enum
class LLMProviderType(Enum):
	"""Types of LLM providers with metadata support."""
	DEEPSEEK = "deepseek"
	OPENAI = "openai"
	ANTHROPIC = "anthropic"
	GOOGLE = "google"
	MISTRAL = "mistral"
	CUSTOM = "custom"
	
	@property
	def display_name(self) -> str:
		"""Get display name for the provider."""
		from app.core.llm_presets import LLMProviderMetadata
		return LLMProviderMetadata.get_provider_config(self.value).get("display_name", self.value)
	
	@property
	def default_api_url(self) -> str:
		"""Get default API URL for the provider."""
		from app.core.llm_presets import LLMProviderMetadata
		return LLMProviderMetadata.get_provider_config(self.value).get("api_url", "")
	
	@property
	def default_api_key_env(self) -> str:
		"""Get default API key environment variable name."""
		from app.core.llm_presets import LLMProviderMetadata
		return LLMProviderMetadata.get_provider_config(self.value).get("api_key_env", "")
	
	@property
	def available_models(self) -> dict:
		"""Get available models for this provider."""
		from app.core.llm_presets import LLMProviderMetadata
		return LLMProviderMetadata.get_available_models(self.value)
	
	def get_model_info(self, model_id: str):
		"""Get information about a specific model."""
		from app.core.llm_presets import LLMProviderMetadata
		return LLMProviderMetadata.get_model_info(self.value, model_id)
	
	def get_models_by_capability(self, capability: str) -> list:
		"""Get model IDs that support a specific capability (text/image/video)."""
		from app.core.llm_presets import LLMProviderMetadata
		return LLMProviderMetadata.get_models_by_capability(self.value, capability)
