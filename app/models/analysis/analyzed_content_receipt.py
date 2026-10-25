"""Durable proof that a source item was successfully analyzed in one context."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped

from ...core.config import settings
from ...core.decorators import app_label
from ..base import Base, TenantScopedMixin


@app_label("analysis")
class AnalyzedContentReceipt(Base, TenantScopedMixin):
    __tablename__ = "analyzed_content_receipts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "source_id", "content_hash", "analysis_fingerprint",
            name="uq_analyzed_content_receipt_identity",
        ),
        Index("ix_analyzed_content_receipts_source", "tenant_id", "source_id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    source_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.sources.id", ondelete="CASCADE"), nullable=False
    )
    content_hash: Mapped[str] = Column(String(64), nullable=False)
    analysis_fingerprint: Mapped[str] = Column(String(64), nullable=False)
    scenario_id: Mapped[int | None] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.agent_scenarios.id", ondelete="SET NULL"), nullable=True
    )
    analytics_id: Mapped[int | None] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.ai_analytics.id", ondelete="SET NULL"), nullable=True
    )
    analyzed_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    if TYPE_CHECKING:
        from ..managers.analyzed_content_receipt_manager import AnalyzedContentReceiptManager
        from ..managers.base_manager import BaseManager

        objects: ClassVar[AnalyzedContentReceiptManager | BaseManager]
    else:
        objects: ClassVar = None
