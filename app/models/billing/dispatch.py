from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped

from ...core.config import settings
from ...core.decorators import app_label
from ..base import Base


@app_label("billing")
class PersonalLLMDispatch(Base):
    """Immutable payer attribution for a prepared dispatch; separate from legacy receipts."""

    __tablename__ = "personal_llm_dispatches"
    __table_args__ = (
        CheckConstraint("dispatch_id ~ '^[0-9a-f]{32}$'", name="ck_personal_llm_dispatch_identity"),
        CheckConstraint("tariff_state IN ('known', 'free', 'undefined')", name="ck_personal_llm_dispatch_tariff"),
        CheckConstraint(
            "(input_cost_per_1k IS NULL OR (input_cost_per_1k >= 0 AND input_cost_per_1k <= 9999999999.99999999)) "
            "AND (output_cost_per_1k IS NULL OR "
            "(output_cost_per_1k >= 0 AND output_cost_per_1k <= 9999999999.99999999)) "
            "AND (cache_read_cost_per_1m IS NULL OR "
            "(cache_read_cost_per_1m >= 0 AND cache_read_cost_per_1m <= 9999999999.99999999)) "
            "AND (cache_write_5m_cost_per_1m IS NULL OR "
            "(cache_write_5m_cost_per_1m >= 0 AND cache_write_5m_cost_per_1m <= 9999999999.99999999)) "
            "AND (cache_write_1h_cost_per_1m IS NULL OR "
            "(cache_write_1h_cost_per_1m >= 0 AND cache_write_1h_cost_per_1m <= 9999999999.99999999))",
            name="ck_personal_llm_dispatch_rates",
        ),
        CheckConstraint(
            "COALESCE(((pricing_policy_version = 'provider-price-v1' "
            "AND (pricing_token_unit IS NULL OR pricing_token_unit = 1000) "
            "AND cache_read_cost_per_1m IS NULL AND cache_write_5m_cost_per_1m IS NULL "
            "AND cache_write_1h_cost_per_1m IS NULL AND ("
            "(tariff_state = 'undefined' AND (input_cost_per_1k IS NULL OR output_cost_per_1k IS NULL)) OR "
            "(tariff_state = 'free' AND input_cost_per_1k IS NOT NULL AND output_cost_per_1k IS NOT NULL "
            "AND input_cost_per_1k = 0 AND output_cost_per_1k = 0) OR "
            "(tariff_state = 'known' AND input_cost_per_1k IS NOT NULL AND output_cost_per_1k IS NOT NULL "
            "AND input_cost_per_1k + output_cost_per_1k > 0))) OR "
            "(pricing_policy_version = 'cache-tariff-v2' AND pricing_token_unit IN (1000, 1000000) "
            "AND ((tariff_state = 'undefined' "
            "AND (input_cost_per_1k IS NULL OR output_cost_per_1k IS NULL)) OR "
            "(tariff_state = 'free' AND input_cost_per_1k IS NOT NULL AND output_cost_per_1k IS NOT NULL "
            "AND cache_read_cost_per_1m IS NOT NULL AND cache_write_5m_cost_per_1m IS NOT NULL "
            "AND cache_write_1h_cost_per_1m IS NOT NULL "
            "AND input_cost_per_1k = 0 AND output_cost_per_1k = 0 "
            "AND cache_read_cost_per_1m = 0 AND cache_write_5m_cost_per_1m = 0 AND cache_write_1h_cost_per_1m = 0) OR "
            "(tariff_state = 'known' AND input_cost_per_1k IS NOT NULL AND output_cost_per_1k IS NOT NULL "
            "AND (cache_read_cost_per_1m IS DISTINCT FROM 0 OR cache_write_5m_cost_per_1m IS DISTINCT FROM 0 "
            "OR cache_write_1h_cost_per_1m IS DISTINCT FROM 0 OR input_cost_per_1k > 0 OR output_cost_per_1k > 0))))), FALSE)",
            name="ck_personal_llm_dispatch_snapshot",
        ),
        CheckConstraint("utc_day = (prepared_at AT TIME ZONE 'UTC')::date", name="ck_personal_llm_dispatch_utc_day"),
        Index("ix_personal_llm_dispatches_account_created", "account_id", "created_at"),
        Index("ix_personal_llm_dispatches_workspace", "tenant_id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    dispatch_id: Mapped[str] = Column(
        String(32),
        nullable=False,
        unique=True,
    )
    account_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.personal_billing_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    payer_user_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False
    )
    initiated_by_user_id: Mapped[int | None] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="RESTRICT"), nullable=True
    )
    tenant_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.tenants.id", ondelete="RESTRICT"), nullable=False
    )
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
    cache_read_cost_per_1m: Mapped[Decimal | None] = Column(Numeric(18, 8), nullable=True)
    cache_write_5m_cost_per_1m: Mapped[Decimal | None] = Column(Numeric(18, 8), nullable=True)
    cache_write_1h_cost_per_1m: Mapped[Decimal | None] = Column(Numeric(18, 8), nullable=True)
    pricing_token_unit: Mapped[int | None] = Column(Integer, nullable=True)
    tariff_state: Mapped[str] = Column(String(16), nullable=False)
    pricing_policy_version: Mapped[str] = Column(String(32), nullable=False)
    source_id: Mapped[int | None] = Column(Integer, nullable=True)
    job_id: Mapped[int | None] = Column(Integer, nullable=True)
    agent_task_id: Mapped[int | None] = Column(Integer, nullable=True)
    agent_session_id: Mapped[int | None] = Column(Integer, nullable=True)
    created_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    if TYPE_CHECKING:
        from ..managers.personal_billing_manager import PersonalBillingManager

        objects: ClassVar[PersonalBillingManager]
    else:
        objects: ClassVar = None
