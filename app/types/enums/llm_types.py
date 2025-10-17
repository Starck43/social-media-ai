"""LLM provider enum types."""
from enum import Enum

from app.utils.db_enums import database_enum

from .content_types import MediaType  # noqa: F401

LLM_STRATEGY_LABELS_RU: dict[str, str] = {
	"cost_efficient": "Экономичная",
	"quality": "Качество",
	"multimodal": "Мультимодальная",
}

LLM_STRATEGY_EMOJI: dict[str, str] = {
	"cost_efficient": "💸",
	"quality": "✨",
	"multimodal": "🖼️",
}


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


@database_enum
class LLMStrategyType(Enum):
	"""LLM strategy selection for bot scenarios."""
	COST_EFFICIENT = "cost_efficient"
	QUALITY = "quality"
	MULTIMODAL = "multimodal"

	@property
	def label(self) -> str:
		return LLM_STRATEGY_LABELS_RU.get(self.value, self.value)

	@property
	def emoji(self) -> str:
		"""Get emoji for the strategy."""
		return LLM_STRATEGY_EMOJI.get(self.value, "")

	@classmethod
	def choices(cls) -> list[tuple[str, str]]:
		"""Get list of tuples containing strategy value and label."""
		return [(m.value, f"{m.emoji} {m.label}".strip()) for m in cls]
