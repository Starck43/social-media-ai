from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Any

from sqlalchemy import Column, Integer, String, Text, Boolean, JSON, ForeignKey, Index
from sqlalchemy.orm import Mapped, relationship

from .base import Base, TenantScopedMixin, TimestampMixin
from ..core.config import settings
from ..core.decorators import app_label
from ..types import BotActionType, BotTriggerType, LLMStrategyType
from ..types.enums.bot_types import AnalyzeType


@app_label("social")
class BotScenario(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "bot_scenarios"
    __table_args__ = (Index("ix_bot_scenarios_tenant_id", "tenant_id"), {"schema": settings.DB_SCHEMA})

    id: Mapped[int] = Column(Integer, primary_key=True)
    name: Mapped[str] = Column(String(255), nullable=False)
    description: Mapped[str] = Column(Text, nullable=True)

    # What content to collect
    content_types: Mapped[list[str]] = Column(JSON, nullable=True, default=list)
    # Which analysis types to apply
    analysis_types: Mapped[list[str]] = Column(JSON, nullable=True, default=list)
    # Configuration parameters for analysis (no analysis_types here!)
    scope: Mapped[dict[str, Any]] = Column(JSON, nullable=True, default=dict)

    # JSON Schema configuration for LLM response format
    analyze_type: Mapped[BotTriggerType] = AnalyzeType.sa_column(
        type_name="analyze_type", nullable=True, store_as_name=False, default=AnalyzeType.THEMES.value
    )

    # Media-specific AI prompt templates with variable substitution support
    # Variables: {text}, {platform}, {source_type}, {stats}, {count}, etc.
    text_prompt: Mapped[str | None] = Column(
        Text, nullable=True, comment="Custom prompt for text analysis. If null, uses default."
    )
    image_prompt: Mapped[str | None] = Column(
        Text, nullable=True, comment="Custom prompt for image analysis. If null, uses default."
    )
    video_prompt: Mapped[str | None] = Column(
        Text, nullable=True, comment="Custom prompt for video analysis. If null, uses default."
    )
    audio_prompt: Mapped[str | None] = Column(
        Text, nullable=True, comment="Custom prompt for audio analysis. If null, uses default."
    )
    unified_summary_prompt: Mapped[str | None] = Column(
        Text, nullable=True, comment="Custom prompt for unified summary. If null, uses default."
    )

    # Backward compatibility property
    @property
    def ai_prompt(self) -> str | None:
        """Legacy property for backward compatibility."""
        return self.text_prompt

    @ai_prompt.setter
    def ai_prompt(self, value: str | None):
        """Legacy setter for backward compatibility."""
        self.text_prompt = value

    # 🆕 Trigger conditions for when to analyze/act
    # Trigger type: when to analyze content or perform action
    trigger_type: Mapped[BotTriggerType] = BotTriggerType.sa_column(
        type_name="bot_trigger_type", nullable=True, store_as_name=True
    )
    # Trigger configuration: parameters for trigger evaluation
    # Examples:
    # - KEYWORD_MATCH: {"keywords": ["жалоба", "проблема"], "mode": "any"}
    # - SENTIMENT_THRESHOLD: {"threshold": 0.3, "direction": "below"}
    # - ACTIVITY_SPIKE: {"baseline_period_hours": 24, "spike_multiplier": 3.0}
    # - USER_MENTION: {"usernames": ["@brand", "@support"]}
    trigger_config: Mapped[dict[str, Any]] = Column(JSON, nullable=True, default=dict)

    # Action to perform after analysis
    action_type: Mapped[BotActionType] = BotActionType.sa_column(
        type_name="bot_action_type", nullable=True, store_as_name=True
    )

    is_active: Mapped[bool] = Column(Boolean, default=True)

    # Collection interval in hours (how often to check and collect content)
    # Used by CheckpointManager to determine if collection is needed
    # Minimum: 1 hour (don't collect more frequently)
    collection_interval_hours: Mapped[int] = Column(Integer, nullable=False, default=1, server_default="1")

    # LLM models for different content types
    #
    # RECOMMENDED: Use llm_strategy (below) for automatic model selection.
    # If set, these explicit models override llm_strategy.
    text_llm_model_id: Mapped[int | None] = Column(
        Integer,
        ForeignKey("social_manager.llm_models.id", ondelete="SET NULL"),
        nullable=True,
        comment="Specific LLM model for text analysis",
    )
    image_llm_model_id: Mapped[int | None] = Column(
        Integer,
        ForeignKey("social_manager.llm_models.id", ondelete="SET NULL"),
        nullable=True,
        comment="Specific LLM model for image analysis",
    )
    video_llm_model_id: Mapped[int | None] = Column(
        Integer,
        ForeignKey("social_manager.llm_models.id", ondelete="SET NULL"),
        nullable=True,
        comment="Specific LLM model for video analysis",
    )

    # LLM resolution strategy: "cost_efficient", “quality”, “multimodal”
    # Used for auto-resolve when explicit FK is not set
    llm_strategy: Mapped[LLMStrategyType] = LLMStrategyType.sa_column(
        type_name="llm_strategy_type", nullable=True, default=LLMStrategyType.COST_EFFICIENT
    )

    # Relationships to LLM models
    text_llm_model: Mapped["LLMModel | None"] = relationship(
        "LLMModel", back_populates="text_scenarios", foreign_keys=[text_llm_model_id]
    )
    image_llm_model: Mapped["LLMModel | None"] = relationship(
        "LLMModel", back_populates="image_scenarios", foreign_keys=[image_llm_model_id]
    )
    video_llm_model: Mapped["LLMModel | None"] = relationship(
        "LLMModel", back_populates="video_scenarios", foreign_keys=[video_llm_model_id]
    )

    # Reverse FK relation: one scenario can be reused by many sources
    sources = relationship("Source", back_populates="bot_scenario")

    # Manager will be set after class definition
    if TYPE_CHECKING:
        from .managers.base_manager import BaseManager
        from .managers.bot_scenario_manager import BotScenarioManager

        objects: ClassVar[BotScenarioManager | BaseManager]
    else:
        objects: ClassVar = None

    def __str__(self) -> str:
        return f"{self.name} ({'active' if self.is_active else 'inactive'})"


from .managers.bot_scenario_manager import BotScenarioManager  # noqa: E402

BotScenario.objects = BotScenarioManager()
