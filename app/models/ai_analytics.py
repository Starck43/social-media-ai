from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import JSON, Column, Date, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, relationship

from ..core.config import settings
from ..core.decorators import app_label
from ..types import PeriodType
from .base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from . import Source


@app_label("dashboard")
class AIAnalytics(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "ai_analytics"
    __table_args__ = (
        UniqueConstraint("source_id", "analysis_date", "period_type", name="uq_analytics_source_date_period"),
        Index("ix_ai_analytics_tenant_id", "tenant_id"),
        Index("idx_ai_analytics_source", "source_id"),
        Index("idx_ai_analytics_date", "analysis_date"),
        Index("idx_ai_analytics_topic_chain", "topic_chain_id"),
        Index("idx_ai_analytics_source_content_hash", "source_id", "content_hash"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    source_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.sources.id", ondelete="CASCADE"), nullable=False
    )
    analysis_date: Mapped[Date] = Column(Date, nullable=True, default=date.today, server_default=text("CURRENT_DATE"))
    # Store as PostgreSQL enum matching the existing DB analysis_period_type type
    period_type: Mapped[PeriodType] = PeriodType.sa_column(
        type_name="analysis_period_type", nullable=False, default=PeriodType.DAY, store_as_name=True
    )
    content_hash: Mapped[str | None] = Column(
        String(64),
        nullable=True,
        comment="SHA256 hex digest of the analyzed content payload for deduplication",
    )

    # Chain tracking for ongoing topics/threads
    topic_chain_id: Mapped[str] = Column(String(100), nullable=True)
    chain_label: Mapped[str | None] = Column(
        String(255),
        nullable=True,
        comment="Human-readable name of the topic chain (latest analysis_title or top topic)",
    )
    normalized_label: Mapped[str | None] = Column(
        String(255),
        nullable=True,
        comment="Normalized topic label for chain deduplication (lowercase, ё→е, stemmed)",
    )
    parent_analysis_id: Mapped[int] = Column(
        ForeignKey(f"{settings.DB_SCHEMA}.ai_analytics.id", ondelete="SET NULL"), nullable=True
    )

    summary_data: Mapped[JSON] = Column(JSON, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    response_payload: Mapped[JSON] = Column(JSON, nullable=True)
    main_topics: Mapped[list[str] | None] = Column(
        JSON, nullable=True, comment="Main topics extracted from AI analysis for theme matching"
    )
    prompt_text: Mapped[str | None] = Column(Text, nullable=True)
    llm_model: Mapped[str | None] = Column(String(100), nullable=True)
    provider_type: Mapped[str | None] = Column(String(100), nullable=True)
    media_types: Mapped[list[str] | None] = Column(JSON, nullable=True)
    request_tokens: Mapped[int | None] = Column(Integer, nullable=True)
    response_tokens: Mapped[int | None] = Column(Integer, nullable=True)
    # Money is exact Decimal, not float or integer cents: cheap models make most
    # analysis calls cost well under a cent, and integer storage rounded those to
    # zero (then dropped them as NULL), so SUM() under-reported daily spend.
    # Unit stays USD cents because every consumer divides by 100.
    estimated_cost: Mapped[Decimal | None] = Column(
        Numeric(14, 6),
        nullable=True,
        comment="Estimated cost in USD cents at 1e-8 USD precision (NULL = unknown or free)",
    )

    # Relationships
    source: Mapped["Source"] = relationship("Source", back_populates="analytics")
    parent: Mapped["AIAnalytics | None"] = relationship(
        "AIAnalytics",
        remote_side="AIAnalytics.id",
        back_populates="children",
        foreign_keys="AIAnalytics.parent_analysis_id",
        passive_deletes=True,
    )
    children: Mapped[list["AIAnalytics"]] = relationship(
        "AIAnalytics", back_populates="parent", cascade="save-update, merge", passive_deletes=True
    )

    # Manager will be set after class definition
    if TYPE_CHECKING:
        from .managers.ai_analytics_manager import AIAnalyticsManager
        from .managers.base_manager import BaseManager

        objects: ClassVar[AIAnalyticsManager | BaseManager]
    else:
        objects: ClassVar = None

    def __str__(self) -> str:
        _date = self.analysis_date
        date_str = _date.strftime("%d-%m-%Y") if _date and isinstance(_date, date) else ""

        return f"Аналитика по {self.source_id} за {date_str}"


from .managers.ai_analytics_manager import AIAnalyticsManager  # noqa: E402

AIAnalytics.objects = AIAnalyticsManager()
