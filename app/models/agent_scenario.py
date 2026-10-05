from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import JSON, Boolean, Column, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, relationship

from ..core.config import settings
from ..core.decorators import app_label
from ..types import LLMStrategyType
from ..types.enums.bot_types import AnalyzeType
from .base import Base, TenantScopedMixin, TimestampMixin


@app_label("social")
class AgentScenario(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "agent_scenarios"
    __table_args__ = (Index("ix_agent_scenarios_tenant_id", "tenant_id"), {"schema": settings.DB_SCHEMA})

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
    analyze_type: Mapped[AnalyzeType | None] = AnalyzeType.sa_column(
        type_name="analyze_type", nullable=True, store_as_name=False, default=AnalyzeType.THEMES.db_value
    )

    # One base prompt for every media type, optional per-media overrides and a
    # summary prompt. Variables: {text}, {platform}, {source_type}, {stats},
    # {count}, {date_range}, {source_name}, {scenario_name}, scope-derived, etc.
    base_prompt: Mapped[str | None] = Column(
        Text, nullable=True, comment="Core LLM instruction for analysis. If null, uses default."
    )
    media_overrides: Mapped[dict[str, str] | None] = Column(
        JSON,
        nullable=True,
        default=dict,
        comment='Per-media-type prompt overrides, e.g. {"image": "...", "video": "..."}',
    )
    summary_prompt: Mapped[str | None] = Column(
        Text, nullable=True, comment="Custom prompt for unified summary. If null, uses default."
    )

    # Backward compatibility property
    @property
    def ai_prompt(self) -> str | None:
        """Legacy property for backward compatibility."""
        return self.base_prompt

    @ai_prompt.setter
    def ai_prompt(self, value: str | None):
        """Legacy setter for backward compatibility."""
        self.base_prompt = value

    # Trigger and action configuration used to live here. It lives on `AgentTask`
    # now: whether to act on an analysis is a property of the run that produced
    # it, not of how it was analysed — two tasks may share this scenario and want
    # different actions or safety limits. See migration 0073.

    # LLM constraints
    max_tokens: Mapped[int | None] = Column(
        Integer, nullable=True, comment="Max tokens for LLM responses in this scenario"
    )
    # JSON Schema for structured output; lazily generated from analysis_types when empty
    output_schema: Mapped[dict[str, Any] | None] = Column(
        JSON, nullable=True, comment="JSON Schema for structured LLM output (lazily generated from analysis_types)"
    )

    is_active: Mapped[bool] = Column(Boolean, default=True)
    is_default: Mapped[bool] = Column(
        Boolean,
        default=False,
        nullable=False,
        server_default="false",
        comment="Default scenario for the tenant when a source has none assigned",
    )

    # LLM models for different content types
    text_llm_model_id: Mapped[int | None] = Column(
        Integer,
        ForeignKey(f"{settings.DB_SCHEMA}.llm_models.id", ondelete="SET NULL"),
        nullable=True,
        comment="Specific LLM model for text analysis",
    )
    image_llm_model_id: Mapped[int | None] = Column(
        Integer,
        ForeignKey(f"{settings.DB_SCHEMA}.llm_models.id", ondelete="SET NULL"),
        nullable=True,
        comment="Specific LLM model for image analysis",
    )
    video_llm_model_id: Mapped[int | None] = Column(
        Integer,
        ForeignKey(f"{settings.DB_SCHEMA}.llm_models.id", ondelete="SET NULL"),
        nullable=True,
        comment="Specific LLM model for video analysis",
    )

    # LLM resolution strategy: "cost_efficient", "quality", "multimodal"
    llm_strategy: Mapped[LLMStrategyType] = LLMStrategyType.sa_column(
        type_name="llm_strategy_type",
        nullable=True,
        default=LLMStrategyType.COST_EFFICIENT.value,
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

    # Reverse FK relation: one scenario can be reused by many tasks
    agent_tasks = relationship("AgentTask", back_populates="agent_scenario")

    # Manager will be set after class definition
    if TYPE_CHECKING:
        from .managers.agent_scenario_manager import AgentScenarioManager
        from .managers.base_manager import BaseManager

        objects: ClassVar[AgentScenarioManager | BaseManager]
    else:
        objects: ClassVar = None

    def __str__(self) -> str:
        return f"{self.name} ({'active' if self.is_active else 'inactive'})"


from .managers.agent_scenario_manager import AgentScenarioManager  # noqa: E402

AgentScenario.objects = AgentScenarioManager()
