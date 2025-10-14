from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Any

from sqlalchemy import Column, Integer, String, Text, Boolean, JSON
from sqlalchemy.orm import Mapped, relationship

from .base import Base, TimestampMixin
from ..core.config import settings
from ..core.decorators import app_label
from ..types import LLMProviderType


@app_label("ai")
class LLMProvider(Base, TimestampMixin):
	__tablename__ = 'llm_providers'
	__table_args__ = {'schema': settings.DB_SCHEMA}

	id: Mapped[int] = Column(Integer, primary_key=True)
	name: Mapped[str] = Column(String(255), nullable=False, unique=True)
	description: Mapped[str] = Column(Text, nullable=True)

	# Provider configuration
	provider_type: Mapped[LLMProviderType] = LLMProviderType.sa_column(
		type_name='llm_provider_type',
		nullable=False,
		store_as_name=False  # Store as values (deepseek, openai) not names (DEEPSEEK, OPENAI)
	)
	api_url: Mapped[str] = Column(String(500), nullable=False)
	# Environment variable name for API key (e.g., "OPENAI_API_KEY")
	api_key_env: Mapped[str] = Column(String(100), nullable=False)
	model_name: Mapped[str] = Column(String(100), nullable=False)

	# Capabilities: ["text", "image", "video"]
	capabilities: Mapped[list[str]] = Column(JSON, nullable=False, default=list)

	# Additional config (temperature, max_tokens, etc.)
	config: Mapped[dict[str, Any]] = Column(JSON, nullable=True, default=dict)

	is_active: Mapped[bool] = Column(Boolean, default=True)

	# Reverse FK relations
	text_scenarios = relationship(
		"BotScenario",
		back_populates="text_llm_provider",
		foreign_keys="BotScenario.text_llm_provider_id"
	)
	image_scenarios = relationship(
		"BotScenario",
		back_populates="image_llm_provider",
		foreign_keys="BotScenario.image_llm_provider_id"
	)
	video_scenarios = relationship(
		"BotScenario",
		back_populates="video_llm_provider",
		foreign_keys="BotScenario.video_llm_provider_id"
	)

	# Manager will be set after class definition
	if TYPE_CHECKING:
		from .managers.base_manager import BaseManager
		from .managers.llm_provider_manager import LLMProviderManager
		objects: ClassVar[LLMProviderManager | BaseManager]
	else:
		objects: ClassVar = None

	def __str__(self) -> str:
		provider_type_str = self.provider_type.value if hasattr(self.provider_type, 'value') else str(self.provider_type)
		capabilities_str = ', '.join(self.capabilities) if self.capabilities else 'none'
		return f"{self.name} ({provider_type_str}) - {capabilities_str}"

	def get_api_key(self) -> str:
		"""Get API key from environment variable."""
		import os
		return os.getenv(self.api_key_env, "")


from .managers.llm_provider_manager import LLMProviderManager  # noqa: E402
LLMProvider.objects = LLMProviderManager()
