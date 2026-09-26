from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, relationship

from ..core.config import settings
from ..core.decorators import app_label
from .base import Base, TimestampMixin


def _model_type_to_capabilities(model_type: str) -> list[str]:
    if model_type == "text":
        return ["text"]
    if model_type == "image":
        return ["text", "image"]
    if model_type == "embedding":
        return ["embedding"]
    return ["text"]


@app_label("ai")
class LLMModel(Base, TimestampMixin):
    __tablename__ = "llm_models"
    __table_args__ = {"schema": settings.DB_SCHEMA}

    id: Mapped[int] = Column(Integer, primary_key=True)
    provider_id: Mapped[int] = Column(
        Integer,
        ForeignKey(f"{settings.DB_SCHEMA}.llm_providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = Column(String(100), nullable=False)
    model_id: Mapped[str] = Column(String(100), nullable=False)
    description: Mapped[str | None] = Column(Text, nullable=True)

    model_type: Mapped[str] = Column(String(20), nullable=False, default="text")

    input_cost_per_1k: Mapped[float] = Column(Float, nullable=False, default=0.0)
    output_cost_per_1k: Mapped[float] = Column(Float, nullable=False, default=0.0)

    last_request_cost: Mapped[float | None] = Column(
        Float, nullable=True, comment="Estimated cost of the last test request (USD)"
    )
    last_request_cost_at: Mapped[datetime | None] = Column(
        DateTime, nullable=True, comment="Timestamp of the last computed estimated cost"
    )

    max_tokens: Mapped[int] = Column(Integer, nullable=False, default=4096)
    default_temperature: Mapped[float] = Column(Float, nullable=False, default=0.3)

    is_active: Mapped[bool] = Column(Boolean, default=True)
    is_default: Mapped[bool] = Column(Boolean, default=False)

    provider = relationship(
        "LLMProvider",
        back_populates="models",
        foreign_keys="LLMModel.provider_id",
    )

    text_scenarios = relationship(
        "AgentScenario",
        back_populates="text_llm_model",
        foreign_keys="AgentScenario.text_llm_model_id",
    )
    image_scenarios = relationship(
        "AgentScenario",
        back_populates="image_llm_model",
        foreign_keys="AgentScenario.image_llm_model_id",
    )
    video_scenarios = relationship(
        "AgentScenario",
        back_populates="video_llm_model",
        foreign_keys="AgentScenario.video_llm_model_id",
    )

    if TYPE_CHECKING:
        from .managers.base_manager import BaseManager
        from .managers.llm_model_manager import LLMModelManager

        objects: ClassVar[LLMModelManager | BaseManager]
    else:
        objects: ClassVar[Any] = None

    @property
    def capabilities(self) -> list[str]:
        return _model_type_to_capabilities(self.model_type)

    def __str__(self) -> str:
        # Avoid triggering lazy load of self.provider in sync contexts
        # (e.g. sqladmin form scaffolding). Check if the relationship
        # is already loaded before accessing it.
        try:
            from sqlalchemy.inspection import inspect
            state = inspect(self)
            if state.session_id is None or "provider" not in state.attrs:
                # Detached or not loaded — don't touch the relationship
                return f"{self.name} ({self.model_id})"
            provider = state.attrs.provider.value
            if provider is None:
                return f"{self.name} ({self.model_id})"
            return f"{provider.name}/{self.model_id} (${self.input_cost_per_1k:.4f}/${self.output_cost_per_1k:.4f}/1K)"
        except Exception:
            return f"{self.name} ({self.model_id})"

    def get_cost_per_million(self) -> tuple[float, float]:
        return self.input_cost_per_1k * 1000, self.output_cost_per_1k * 1000

    def can_handle(self, capability: str) -> bool:
        return capability in self.capabilities


from .managers.llm_model_manager import LLMModelManager  # noqa: E402

LLMModel.objects = LLMModelManager()
