from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, ForeignKeyConstraint, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, relationship

from ...core.config import settings
from ...core.decorators import app_label
from ..base import Base, TimestampMixin


def _model_type_to_capabilities(model_type: str) -> list[str]:
    if not model_type:
        return []
    return [c.strip() for c in model_type.split(",")]


@app_label("llm")
class LLMModel(Base, TimestampMixin):
    __tablename__ = "llm_models"
    __personal_scoped__ = True
    __table_args__ = (
        ForeignKeyConstraint(
            ["provider_id", "owner_user_id"],
            [f"{settings.DB_SCHEMA}.llm_providers.id", f"{settings.DB_SCHEMA}.llm_providers.owner_user_id"],
            name="fk_llm_model_provider_owner", ondelete="CASCADE",
        ),
        CheckConstraint(
            "(cache_read_cost_per_1m IS NULL OR cache_read_cost_per_1m >= 0) "
            "AND (cache_write_5m_cost_per_1m IS NULL OR cache_write_5m_cost_per_1m >= 0) "
            "AND (cache_write_1h_cost_per_1m IS NULL OR cache_write_1h_cost_per_1m >= 0)",
            name="ck_llm_models_cache_rates",
        ),
        CheckConstraint("pricing_token_unit IN (1000, 1000000)", name="ck_llm_models_pricing_token_unit"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    provider_id: Mapped[int] = Column(
        Integer,
        nullable=False,
        index=True,
    )

    owner_user_id: Mapped[int] = Column(
        ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = Column(String(100), nullable=False)
    model_id: Mapped[str] = Column(String(100), nullable=False)
    description: Mapped[str | None] = Column(Text, nullable=True)

    model_type: Mapped[str] = Column(String(20), nullable=False, default="text")

    custom_endpoint_path: Mapped[str | None] = Column(
        String(200), nullable=True, comment="Custom endpoint path for api_format=custom, e.g. /v1/systemone"
    )

    input_cost_per_1k: Mapped[float] = Column(Float, nullable=False, default=0.0)
    output_cost_per_1k: Mapped[float] = Column(Float, nullable=False, default=0.0)
    cache_read_cost_per_1m: Mapped[Decimal | None] = Column(Numeric(18, 8), nullable=True)
    cache_write_5m_cost_per_1m: Mapped[Decimal | None] = Column(Numeric(18, 8), nullable=True)
    cache_write_1h_cost_per_1m: Mapped[Decimal | None] = Column(Numeric(18, 8), nullable=True)
    pricing_token_unit: Mapped[int] = Column(Integer, nullable=False, default=1_000_000, server_default="1000000")

    last_request_cost: Mapped[float | None] = Column(
        Float, nullable=True, comment="Estimated cost of the last test request (USD)"
    )
    last_request_cost_at: Mapped[datetime | None] = Column(
        DateTime, nullable=True, comment="Timestamp of the last computed estimated cost"
    )

    # Phase 2 — usage/health counters (backfilled from ai_analytics history)
    last_used_at: Mapped[datetime | None] = Column(DateTime, nullable=True, comment="Last time this model was invoked")
    last_success_at: Mapped[datetime | None] = Column(
        DateTime, nullable=True, comment="Last successful invocation (no 5xx/timeout)"
    )
    last_error_at: Mapped[datetime | None] = Column(
        DateTime, nullable=True, comment="Last failed invocation (5xx/timeout/network)"
    )
    use_count: Mapped[int] = Column(Integer, nullable=False, default=0, server_default="0")
    fail_count: Mapped[int] = Column(Integer, nullable=False, default=0, server_default="0")

    max_tokens: Mapped[int] = Column(Integer, nullable=False, default=4096)
    default_temperature: Mapped[float] = Column(Float, nullable=False, default=0.3)

    is_active: Mapped[bool] = Column(Boolean, default=True)
    is_default: Mapped[bool] = Column(Boolean, default=False)

    provider = relationship(
        "LLMProvider",
        back_populates="models",
        foreign_keys="[LLMModel.provider_id, LLMModel.owner_user_id]",
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
        from ..managers.base_manager import BaseManager
        from ..managers.llm_model_manager import LLMModelManager

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
            unit = "1K" if getattr(self, "pricing_token_unit", 1000) == 1000 else "1M"
            return f"{provider.name}/{self.model_id} (${self.input_cost_per_1k}/${self.output_cost_per_1k}/{unit})"
        except Exception:
            return f"{self.name} ({self.model_id})"

    def get_cost_per_million(self) -> tuple[float, float]:
        divisor = getattr(self, "pricing_token_unit", 1000) or 1000
        return (
            float(Decimal(str(self.input_cost_per_1k)) * Decimal(1_000_000) / Decimal(divisor)),
            float(Decimal(str(self.output_cost_per_1k)) * Decimal(1_000_000) / Decimal(divisor)),
        )

    def can_handle(self, capability: str) -> bool:
        return capability in self.capabilities
