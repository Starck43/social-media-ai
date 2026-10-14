"""Unwired payer/day balances and insert-only receipt-bound reservations."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import ClassVar

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
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
class LLMBudgetDay(Base, TenantScopedMixin):
    """Explicit reviewed baseline only; no runtime provisioning or expiry refunds."""

    __tablename__ = "llm_budget_days"
    __table_args__ = (
        UniqueConstraint("tenant_id", "utc_day", name="uq_llm_budget_days_payer_day"),
        CheckConstraint(
            "settled_usd >= 0 AND settled_usd <= 999999999.999999999 AND reserved_usd >= 0 AND reserved_usd "
            "<= 999999999.999999999",
            name="ck_llm_budget_days_amounts",
        ),
        CheckConstraint("undefined_count >= 0 AND unbounded_count >= 0", name="ck_llm_budget_days_counts"),
        {"schema": settings.DB_SCHEMA},
    )
    id: Mapped[int] = Column(Integer, primary_key=True)
    tenant_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.tenants.id", ondelete="RESTRICT"), nullable=False
    )
    utc_day: Mapped[date] = Column(Date, nullable=False)
    settled_usd: Mapped[Decimal] = Column(Numeric(18, 9), nullable=False)
    reserved_usd: Mapped[Decimal] = Column(Numeric(18, 9), nullable=False)
    undefined_count: Mapped[int] = Column(Integer, nullable=False)
    unbounded_count: Mapped[int] = Column(Integer, nullable=False)
    frozen: Mapped[bool] = Column(Boolean, nullable=False, default=True, server_default="true")
    baseline_version: Mapped[str] = Column(String(64), nullable=False)
    objects: ClassVar = None


@app_label("ai")
class LLMBudgetReservation(Base, TenantScopedMixin):
    """Original bound/policy/cap snapshot; no terminal release in this slice."""

    __tablename__ = "llm_budget_reservations"
    __table_args__ = (
        UniqueConstraint("dispatch_id", name="uq_llm_budget_reservations_identity"),
        ForeignKeyConstraint(
            ["tenant_id", "utc_day"],
            [f"{settings.DB_SCHEMA}.llm_budget_days.tenant_id", f"{settings.DB_SCHEMA}.llm_budget_days.utc_day"],
            ondelete="RESTRICT",
            name="fk_llm_budget_reservations_day",
        ),
        CheckConstraint("dispatch_id ~ '^[0-9a-f]{32}$'", name="ck_llm_budget_reservations_identity"),
        CheckConstraint(
            "ceiling_state IN ('bounded', 'unbounded', 'undefined')",
            name="ck_llm_budget_reservations_state",
        ),
        CheckConstraint(
            "(ceiling_usd IS NULL OR (ceiling_usd >= 0 AND ceiling_usd <= 999999999.999999999)) AND (cap_usd "
            "IS NULL OR (cap_usd > 0 AND cap_usd <= 999999999.999999999))",
            name="ck_llm_budget_reservations_amounts",
        ),
        CheckConstraint(
            "(ceiling_state = 'bounded' AND ceiling_usd IS NOT NULL AND (ceiling_usd = 0 OR bound_version IS "
            "NOT NULL)) OR (ceiling_state = 'unbounded' AND ceiling_usd IS NULL AND bound_version IS NULL AND "
            "cap_usd IS NULL) OR (ceiling_state = 'undefined' AND ceiling_usd IS NOT NULL AND ceiling_usd = 0 "
            "AND bound_version IS NULL)",
            name="ck_llm_budget_reservations_bound",
        ),
        CheckConstraint(
            "undefined_policy_version = 'owner-undefined-allow-v1'",
            name="ck_llm_budget_reservations_policy",
        ),
        {"schema": settings.DB_SCHEMA},
    )
    id: Mapped[int] = Column(Integer, primary_key=True)
    tenant_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.tenants.id", ondelete="RESTRICT"), nullable=False
    )
    utc_day: Mapped[date] = Column(Date, nullable=False)
    dispatch_id: Mapped[str] = Column(
        String(32),
        ForeignKey(f"{settings.DB_SCHEMA}.llm_dispatch_receipts.dispatch_id", ondelete="RESTRICT"),
        nullable=False,
    )
    ceiling_state: Mapped[str] = Column(String(16), nullable=False)
    ceiling_usd: Mapped[Decimal | None] = Column(Numeric(18, 9), nullable=True)
    bound_version: Mapped[str | None] = Column(String(64), nullable=True)
    undefined_policy_version: Mapped[str] = Column(String(32), nullable=False)
    cap_usd: Mapped[Decimal | None] = Column(Numeric(18, 9), nullable=True)
    objects: ClassVar = None
