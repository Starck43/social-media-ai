from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Any

from sqlalchemy import Column, Integer, String, Text, Boolean, JSON
from sqlalchemy.orm import Mapped, relationship

from .base import Base, TimestampMixin
from ..core.config import settings
from ..core.decorators import app_label


@app_label("ai")
class LLMProvider(Base, TimestampMixin):
	__tablename__ = 'llm_providers'
	__table_args__ = {'schema': settings.DB_SCHEMA}

	id: Mapped[int] = Column(Integer, primary_key=True)
	name: Mapped[str] = Column(String(255), nullable=False, unique=True)
	description: Mapped[str] = Column(Text, nullable=True)

	# provider_type removed - determined by api_url in LLMClient.create()
	api_url: Mapped[str] = Column(String(500), nullable=False)
	# Environment variable name for API key (e.g., "OPENAI_API_KEY")
	api_key_env: Mapped[str] = Column(String(100), nullable=False)

	# Additional config for provider (default settings, rate limits, etc.)
	config: Mapped[dict[str, Any]] = Column(JSON, nullable=True, default=dict)

	is_active: Mapped[bool] = Column(Boolean, default=True)

	# Relationships
	# One provider can have many models
	models = relationship(
		"LLMModel",
		back_populates="provider",
		cascade="all, delete-orphan",
		foreign_keys="LLMModel.provider_id"
	)

	# Manager will be set after class definition
	if TYPE_CHECKING:
		from .managers.base_manager import BaseManager
		from .managers.llm_provider_manager import LLMProviderManager
		objects: ClassVar[LLMProviderManager | BaseManager]
	else:
		objects: ClassVar = None

	def __str__(self) -> str:
		return f"{self.name}"

	def get_api_key(self) -> str:
		"""Get API key from environment variable."""
		import os
		return os.getenv(self.api_key_env, "")


from .managers.llm_provider_manager import LLMProviderManager  # noqa: E402

LLMProvider.objects = LLMProviderManager()
