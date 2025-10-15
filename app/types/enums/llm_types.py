"""LLM provider enum types."""
from enum import Enum

from app.utils.db_enums import database_enum

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


class MediaType(str, Enum):
	"""Media content types for LLM processing."""
	TEXT = "text"
	IMAGE = "image"
	VIDEO = "video"
	AUDIO = "audio"  # For future use
	
	def __str__(self) -> str:
		"""Return string value for compatibility."""
		return self.value
	
	@property
	def label(self) -> str:
		"""Get human-readable label."""
		labels = {
			"text": "Текст",
			"image": "Изображение",
			"video": "Видео",
			"audio": "Аудио"
		}
		return labels.get(self.value, self.value)
	
	@property
	def description(self) -> str:
		"""Get description for the media type."""
		descriptions = {
			"text": "Текстовый анализ постов, комментариев, описаний",
			"image": "Анализ изображений и фотографий",
			"video": "Анализ видеоконтента",
			"audio": "Анализ аудио и голосовых сообщений"
		}
		return descriptions.get(self.value, "")
	
	@classmethod
	def from_string(cls, value: str) -> "MediaType":
		"""Convert string to MediaType enum."""
		value_lower = value.lower()
		for media_type in cls:
			if media_type.value == value_lower:
				return media_type
		raise ValueError(f"Invalid media type: {value}")
	
	@classmethod
	def values(cls) -> list[str]:
		"""Get all media type values as list."""
		return [mt.value for mt in cls]
	
	@classmethod
	def choices(cls) -> list[tuple[str, str]]:
		"""
		Get choices for form fields.
		
		Returns:
			List of (value, label) tuples for SelectMultipleField
		"""
		return [(mt.value, mt.label) for mt in cls]
	
	@classmethod
	def choices_with_descriptions(cls) -> list[dict[str, str]]:
		"""
		Get choices with descriptions for admin forms.
		
		Returns:
			List of dicts with value, label and description
		"""
		return [
			{
				"value": mt.value,
				"label": mt.label,
				"description": mt.description
			}
			for mt in cls
		]


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
