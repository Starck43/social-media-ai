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
