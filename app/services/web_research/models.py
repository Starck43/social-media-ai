"""Durable tenant-scoped quota state for web research (P20-T12)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base, TenantScopedMixin


class WebResearchQuota(Base, TenantScopedMixin):
    """Per-(tenant_id, account_key) atomic daily quota and token-bucket state."""

    __tablename__ = "web_research_quotas"
    __table_args__ = (
        CheckConstraint(
            "daily_used >= 0 AND daily_quota > 0 AND daily_used <= daily_quota",
            name="ck_web_research_quotas_daily",
        ),
        CheckConstraint(
            "burst_tokens >= 0 AND burst_limit > 0 AND rate_per_minute > 0",
            name="ck_web_research_quotas_burst",
        ),
        CheckConstraint(
            "length(account_key) >= 1 AND length(account_key) <= 80",
            name="ck_web_research_quotas_account_key",
        ),
        CheckConstraint(
            "length(day_key) = 10",
            name="ck_web_research_quotas_day_key",
        ),
    )

    tenant_id: Mapped[int] = mapped_column(
        ForeignKey(f"{settings.DB_SCHEMA}.tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    account_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    day_key: Mapped[str] = mapped_column(String(10), nullable=False)
    daily_used: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_quota: Mapped[int] = mapped_column(Integer, nullable=False)
    burst_tokens: Mapped[float] = mapped_column(Float, nullable=False)
    burst_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    rate_per_minute: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
