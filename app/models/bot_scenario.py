from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Dict, Any

from sqlalchemy import Column, Integer, String, Text, Boolean, JSON, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, relationship

from .base import Base, TimestampMixin
from ..core.config import settings
from ..core.decorators import app_label
from ..types import BotActionType


@app_label("social")
class BotScenario(Base, TimestampMixin):
	__tablename__ = 'bot_scenarios'
	__table_args__ = {'schema': settings.DB_SCHEMA}

	id: Mapped[int] = Column(Integer, primary_key=True)
	name: Mapped[str] = Column(String(255), nullable=False)
	description: Mapped[str] = Column(Text, nullable=True)
	
	# What content to collect
	content_types: Mapped[list[str]] = Column(JSON, nullable=False, default=list)
	# Which analysis types to apply
	analysis_types: Mapped[list[str]] = Column(JSON, nullable=False, default=list)
	# Configuration parameters for analysis (no analysis_types here!)
	scope: Mapped[dict[str, Any]] = Column(JSON, nullable=True, default=dict)
	
	# AI prompt template with variables
	ai_prompt: Mapped[str] = Column(Text, nullable=True)
	
	# Action to perform after analysis
	action_type: Mapped[BotActionType] = BotActionType.sa_column(
		type_name='bot_action_type',
		nullable=True,
		store_as_name=True
	)
	
	is_active: Mapped[bool] = Column(Boolean, default=True)
	cooldown_minutes: Mapped[int] = Column(Integer, default=30)

	# LLM providers for different content types
	# LEGACY: Individual FK fields (kept for backward compatibility)
	text_llm_provider_id: Mapped[int | None] = Column(
		Integer,
		ForeignKey("social_manager.llm_providers.id", ondelete="SET NULL"),
		nullable=True
	)
	image_llm_provider_id: Mapped[int | None] = Column(
		Integer,
		ForeignKey("social_manager.llm_providers.id", ondelete="SET NULL"),
		nullable=True
	)
	video_llm_provider_id: Mapped[int | None] = Column(
		Integer,
		ForeignKey("social_manager.llm_providers.id", ondelete="SET NULL"),
		nullable=True
	)
	
	# NEW: Flexible LLM mapping (JSON format)
	# Structure: {"text": {"provider_id": 1, "model_id": "gpt-4", "provider_type": "openai"}, ...}
	# Allows using same provider with different models for different media types
	llm_mapping: Mapped[dict[str, Any]] = Column(JSON, nullable=True, default=dict)
	
	# LLM resolution strategy: "cost_efficient", "quality", "multimodal"
	llm_strategy: Mapped[str] = Column(String(50), nullable=True, default="cost_efficient")

	# Relationships to LLM providers
	text_llm_provider: Mapped["LLMProvider | None"] = relationship(
		"LLMProvider",
		back_populates="text_scenarios",
		foreign_keys="BotScenario.text_llm_provider_id"
	)
	image_llm_provider: Mapped["LLMProvider | None"] = relationship(
		"LLMProvider",
		back_populates="image_scenarios",
		foreign_keys="BotScenario.image_llm_provider_id"
	)
	video_llm_provider: Mapped["LLMProvider | None"] = relationship(
		"LLMProvider",
		back_populates="video_scenarios",
		foreign_keys="BotScenario.video_llm_provider_id"
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
