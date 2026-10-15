"""Immutable, receipt-bound normalized price and outcome observations."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import (
    CheckConstraint,
    Column,
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


@app_label("llm")
class LLMOutcomeEvent(Base, TenantScopedMixin):
    """An append-only observation, not a spend projection or settlement grant.

    Several observations/reconciliations can belong to one receipt. NEVER sum
    their costs as independent attempts. Unknown outcome stays unknown; no
    prompt, credentials or raw provider payload is stored.
    """

    __tablename__ = "llm_outcome_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_llm_outcome_events_identity"),
        CheckConstraint(
            "event_id ~ '^[0-9a-f]{32}$' AND dispatch_id ~ '^[0-9a-f]{32}$'", name="ck_llm_outcome_events_identity"
        ),
        CheckConstraint("event_kind IN ('observation', 'reconciliation')", name="ck_llm_outcome_events_kind"),
        CheckConstraint(
            "provider_price_state IN ('absent', 'resolved', 'unresolved') AND cost_certainty IN ('priced', 'free', 'undefined', 'not_dispatched') AND price_source IN ('provider', 'receipt_tariff', 'none', 'not_dispatched')",
            name="ck_llm_outcome_events_states",
        ),
        CheckConstraint(
            "(dispatch_state = 'not_dispatched' AND outcome = 'not_dispatched') OR (dispatch_state = 'possible' AND outcome IN ('failed_transport', 'timeout', 'cancelled', 'unknown')) OR (dispatch_state = 'response_received' AND outcome IN ('succeeded', 'failed_output', 'failed_transport', 'timeout', 'cancelled', 'unknown'))",
            name="ck_llm_outcome_events_outcome",
        ),
        CheckConstraint(
            "(prompt_tokens IS NULL OR prompt_tokens >= 0) AND (completion_tokens IS NULL OR completion_tokens >= 0)",
            name="ck_llm_outcome_events_usage",
        ),
        CheckConstraint(
            "cost_usd IS NULL OR (cost_usd >= 0 AND cost_usd <= 999999999.999999999)",
            name="ck_llm_outcome_events_amount",
        ),
        CheckConstraint(
            "(cost_certainty IN ('undefined', 'not_dispatched') AND cost_usd IS NULL) OR (cost_certainty = 'free' AND cost_usd IS NOT NULL AND cost_usd = 0) OR (cost_certainty = 'priced' AND cost_usd IS NOT NULL)",
            name="ck_llm_outcome_events_certainty",
        ),
        CheckConstraint(
            "(provider_price_state = 'absent' AND provider_cost_raw IS NULL AND price_source IN ('receipt_tariff', 'none', 'not_dispatched')) OR (provider_price_state = 'resolved' AND provider_cost_raw IS NOT NULL AND price_source = 'provider' AND cost_certainty IN ('priced', 'free')) OR (provider_price_state = 'unresolved' AND price_source = 'provider' AND cost_certainty = 'undefined')",
            name="ck_llm_outcome_events_report",
        ),
        CheckConstraint(
            "(dispatch_state = 'not_dispatched' AND cost_certainty = 'not_dispatched' AND price_source = 'not_dispatched' AND provider_price_state = 'absent' AND prompt_tokens IS NULL AND completion_tokens IS NULL) OR (dispatch_state <> 'not_dispatched' AND cost_certainty <> 'not_dispatched' AND price_source <> 'not_dispatched')",
            name="ck_llm_outcome_events_no_dispatch",
        ),
        CheckConstraint(
            "provider_cost_raw IS NULL OR provider_cost_raw ~ '^[0-9]+([.][0-9]+)?([Ee][+-]?[0-9]+)?$'",
            name="ck_llm_outcome_events_raw_decimal",
        ),
        CheckConstraint(
            "price_source <> 'none' OR (cost_usd IS NULL AND cost_certainty = 'undefined')",
            name="ck_llm_outcome_events_no_price",
        ),
        Index("ix_llm_outcome_events_tenant_receipt", "tenant_id", "dispatch_id", "observed_at"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    tenant_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.tenants.id", ondelete="RESTRICT"), nullable=False
    )
    event_id: Mapped[str] = Column(String(32), nullable=False)
    dispatch_id: Mapped[str] = Column(
        String(32),
        ForeignKey(f"{settings.DB_SCHEMA}.llm_dispatch_receipts.dispatch_id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_kind: Mapped[str] = Column(String(32), nullable=False)
    observed_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False)
    dispatch_state: Mapped[str] = Column(String(32), nullable=False)
    outcome: Mapped[str] = Column(String(32), nullable=False)
    prompt_tokens: Mapped[int | None] = Column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = Column(Integer, nullable=True)
    provider_price_state: Mapped[str] = Column(String(16), nullable=False)
    provider_cost_raw: Mapped[str | None] = Column(String(128), nullable=True)
    cost_usd: Mapped[Decimal | None] = Column(Numeric(18, 9), nullable=True)
    cost_certainty: Mapped[str] = Column(String(32), nullable=False)
    price_source: Mapped[str] = Column(String(32), nullable=False)
    pricing_policy_version: Mapped[str] = Column(String(32), nullable=False)
    created_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    if TYPE_CHECKING:
        from ..managers.llm_outcome_event_manager import LLMOutcomeEventManager

        objects: ClassVar[LLMOutcomeEventManager]
    else:
        objects: ClassVar = None
