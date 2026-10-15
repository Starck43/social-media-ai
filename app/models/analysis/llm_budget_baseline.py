"""Immutable initial day snapshot and retained operator-review provenance."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped

from ...core.config import settings
from ...core.decorators import app_label
from ..base import Base, TenantScopedMixin


@app_label("ai")
class LLMBudgetBaseline(Base, TenantScopedMixin):
    """Insert-only review packet, not a current balance or worker-fence executor."""

    __tablename__ = "llm_budget_baselines"
    __table_args__ = (
        UniqueConstraint("tenant_id", "utc_day", name="uq_llm_budget_baselines_payer_day"),
        ForeignKeyConstraint(
            ["tenant_id", "utc_day"],
            [f"{settings.DB_SCHEMA}.llm_budget_days.tenant_id", f"{settings.DB_SCHEMA}.llm_budget_days.utc_day"],
            ondelete="RESTRICT",
            name="fk_llm_budget_baselines_day",
        ),
        CheckConstraint(
            "settled_usd >= 0 AND settled_usd <= 999999999.999999999 AND reserved_usd >= 0 AND reserved_usd "
            "<= 999999999.999999999",
            name="ck_llm_budget_baselines_amounts",
        ),
        CheckConstraint("undefined_count >= 0 AND unbounded_count >= 0", name="ck_llm_budget_baselines_counts"),
        CheckConstraint("frozen", name="ck_llm_budget_baselines_frozen"),
        CheckConstraint(
            "baseline_version ~ '^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}$' AND "
            "reviewed_by ~ '^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}$' AND "
            "review_ref ~ '^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}$' AND "
            "fence_ref ~ '^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}$' AND "
            "evidence_sha256 ~ '^[0-9a-f]{64}$' AND fence_version = 'quiesced-accounted-v1'",
            name="ck_llm_budget_baselines_review",
        ),
        CheckConstraint(
            "utc_day >= DATE '0001-01-01' AND utc_day < DATE '9999-12-31' AND "
            "cutoff_at >= (utc_day::timestamp AT TIME ZONE 'UTC') AND "
            "cutoff_at <= ((utc_day + 1)::timestamp AT TIME ZONE 'UTC') AND reviewed_at >= cutoff_at",
            name="ck_llm_budget_baselines_times",
        ),
        {"schema": settings.DB_SCHEMA},
    )
    id: Mapped[int] = Column(Integer, primary_key=True)
    tenant_id: Mapped[int] = Column(Integer, nullable=False)
    utc_day: Mapped[date] = Column(Date, nullable=False)
    settled_usd: Mapped[Decimal] = Column(Numeric(18, 9), nullable=False)
    reserved_usd: Mapped[Decimal] = Column(Numeric(18, 9), nullable=False)
    undefined_count: Mapped[int] = Column(Integer, nullable=False)
    unbounded_count: Mapped[int] = Column(Integer, nullable=False)
    frozen: Mapped[bool] = Column(Boolean, nullable=False)
    baseline_version: Mapped[str] = Column(String(64), nullable=False)
    reviewed_by: Mapped[str] = Column(String(64), nullable=False)
    review_ref: Mapped[str] = Column(String(64), nullable=False)
    evidence_sha256: Mapped[str] = Column(String(64), nullable=False)
    cutoff_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False)
    reviewed_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False)
    fence_ref: Mapped[str] = Column(String(64), nullable=False)
    fence_version: Mapped[str] = Column(String(32), nullable=False)
    objects: ClassVar = None
