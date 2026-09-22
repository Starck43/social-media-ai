from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Any

from sqlalchemy import Column, Integer, String, Text, Boolean, JSON, Float, ForeignKey
from sqlalchemy.orm import Mapped, relationship

from .base import Base, TimestampMixin
from ..core.config import settings
from ..core.decorators import app_label


@app_label("ai")
class LLMModel(Base, TimestampMixin):
	__tablename__ = 'llm_models'
	__table_args__ = {'schema': settings.DB_SCHEMA}

	id: Mapped[int] = Column(Integer, primary_key=True)
	provider_id: Mapped[int] = Column(
		Integer,
		ForeignKey(f"{settings.DB_SCHEMA}.llm_providers.id", ondelete="CASCADE"),
		nullable=False,
		index=True
	)
	name: Mapped[str] = Column(String(100), nullable=False)
	description: Mapped[str] = Column(Text, nullable=True)

	# Cost configuration
	input_cost: Mapped[float] = Column(Float, nullable=False)  # $ per 1M input tokens
	output_cost: Mapped[float] = Column(Float, nullable=False)  # $ per 1M output tokens

	# Capabilities: ["text", "image", "video"]
	capabilities: Mapped[list[str]] = Column(JSON, nullable=False, default=list)

	# Additional config (temperature, max_tokens, etc.)
	config: Mapped[dict[str, Any]] = Column(JSON, nullable=True, default=dict)

	# Model settings
	is_active: Mapped[bool] = Column(Boolean, default=True)
	is_default: Mapped[bool] = Column(Boolean, default=False)

	# Relationships
	# FK to provider (many models belong to one provider)
	provider = relationship(
		"LLMProvider",
		back_populates="models",
		foreign_keys="LLMModel.provider_id"
	)
	
	# Reverse FK relations - scenarios that use this model
	text_scenarios = relationship(
		"BotScenario",
		back_populates="text_llm_model",
		foreign_keys="BotScenario.text_llm_model_id"
	)
	image_scenarios = relationship(
		"BotScenario",
		back_populates="image_llm_model",
		foreign_keys="BotScenario.image_llm_model_id"
	)
	video_scenarios = relationship(
		"BotScenario",
		back_populates="video_llm_model",
		foreign_keys="BotScenario.video_llm_model_id"
	)

	# Manager will be set after class definition
	if TYPE_CHECKING:
		from .managers.base_manager import BaseManager
		from .managers.llm_model_manager import LLMModelManager
		objects: ClassVar[LLMModelManager | BaseManager]
	else:
		objects: ClassVar = None

	def __str__(self) -> str:
		capabilities_str = ', '.join(self.capabilities) if self.capabilities else 'none'
		return f"{self.name} (${self.input_cost}/${self.output_cost} per 1M) - {capabilities_str}"

	def get_cost_per_million(self) -> tuple[float, float]:
		"""Return (input_cost, output_cost) per million tokens."""
		return self.input_cost, self.output_cost

	def can_handle(self, capability: str) -> bool:
		"""Check if model supports given capability."""
		return capability in self.capabilities


from .managers.llm_model_manager import LLMModelManager  # noqa: E402

LLMModel.objects = LLMModelManager()
