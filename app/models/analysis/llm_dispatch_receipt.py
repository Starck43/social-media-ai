"""Immutable preparation receipts, not proof of provider dispatch or charge."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped

from ...core.config import settings
from ...core.decorators import app_label
from ..base import Base, TenantScopedMixin


@app_label("ai")
class LLMDispatchReceipt(Base, TenantScopedMixin):
    """One preparation identity with a frozen tariff basis and explicit payer.

    Execution/model references are plain integers so deletion/reconfiguration
    cannot rewrite evidence. Payer deletion is restricted, not cascading.
    Migration-defined triggers reject UPDATE, DELETE and TRUNCATE; no runtime
    table/trigger creation is performed. No reservation or actual cost is claimed.
    """

    __tablename__ = "llm_dispatch_receipts"
    __table_args__ = (
        UniqueConstraint("dispatch_id", name="uq_llm_dispatch_receipts_identity"),
        CheckConstraint("dispatch_id ~ '^[0-9a-f]{32}$'", name="ck_llm_dispatch_receipts_identity"),
        CheckConstraint("tariff_state IN ('known', 'free', 'undefined')", name="ck_llm_dispatch_receipts_tariff_state"),
        CheckConstraint("utc_day = (prepared_at AT TIME ZONE 'UTC')::date", name="ck_llm_dispatch_receipts_utc_day"),
        CheckConstraint(
            "(input_cost_per_1k IS NULL OR (input_cost_per_1k >= 0 AND input_cost_per_1k <= 9999999999.99999999)) "
            "AND (output_cost_per_1k IS NULL OR "
            "(output_cost_per_1k >= 0 AND output_cost_per_1k <= 9999999999.99999999))",
            name="ck_llm_dispatch_receipts_rates",
        ),
        CheckConstraint(
            "(tariff_state = 'undefined' AND (input_cost_per_1k IS NULL OR output_cost_per_1k IS NULL)) OR "
            "(tariff_state = 'free' AND input_cost_per_1k IS NOT NULL AND output_cost_per_1k IS NOT NULL "
            "AND input_cost_per_1k = 0 AND output_cost_per_1k = 0) OR "
            "(tariff_state = 'known' AND input_cost_per_1k IS NOT NULL AND output_cost_per_1k IS NOT NULL "
            "AND input_cost_per_1k + output_cost_per_1k > 0)",
            name="ck_llm_dispatch_receipts_tariff_snapshot",
        ),
        Index("ix_llm_dispatch_receipts_tenant_day", "tenant_id", "utc_day"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    tenant_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.tenants.id", ondelete="RESTRICT"), nullable=False
    )
    dispatch_id: Mapped[str] = Column(String(32), nullable=False)
    purpose: Mapped[str] = Column(String(32), nullable=False)
    stage: Mapped[str] = Column(String(32), nullable=False)
    prepared_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False)
    utc_day: Mapped[date] = Column(Date, nullable=False)
    llm_model_row_id: Mapped[int] = Column(Integer, nullable=False)
    llm_provider_row_id: Mapped[int] = Column(Integer, nullable=False)
    model_id: Mapped[str] = Column(String(200), nullable=False)
    provider_name: Mapped[str] = Column(String(200), nullable=False)
    input_cost_per_1k: Mapped[Decimal | None] = Column(Numeric(18, 8), nullable=True)
    output_cost_per_1k: Mapped[Decimal | None] = Column(Numeric(18, 8), nullable=True)
    tariff_state: Mapped[str] = Column(String(16), nullable=False)
    pricing_policy_version: Mapped[str] = Column(String(32), nullable=False)
    source_id: Mapped[int | None] = Column(Integer, nullable=True)
    job_id: Mapped[int | None] = Column(Integer, nullable=True)
    agent_task_id: Mapped[int | None] = Column(Integer, nullable=True)
    agent_session_id: Mapped[int | None] = Column(Integer, nullable=True)
    created_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    if TYPE_CHECKING:
        from ..managers.llm_dispatch_receipt_manager import LLMDispatchReceiptManager

        objects: ClassVar[LLMDispatchReceiptManager]
    else:
        objects: ClassVar = None
