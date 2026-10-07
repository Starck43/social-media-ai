"""LLM provider enum types."""

from enum import Enum

from app.utils.db_enums import DatabaseEnum, database_enum

from .content_types import MediaType  # noqa: F401

API_FORMAT_LABELS: dict[str, str] = {
    "openai": "OpenAI-compatible (/chat/completions)",
    "anthropic": "Anthropic Messages (/messages)",
}


class APIFormatType(str, Enum):
    """Supported LLM API formats (stored as plain string in llm_providers.api_format)."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"

    @property
    def label(self) -> str:
        return API_FORMAT_LABELS.get(self.value, self.value)

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        """Get list of (value, label) tuples for form select fields."""
        return [(m.value, m.label) for m in cls]

    @classmethod
    def values(cls) -> list[str]:
        return [m.value for m in cls]


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
class LLMStrategyType(DatabaseEnum, Enum):
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

    def __str__(self) -> str:
        return self.value
