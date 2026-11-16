"""Owner-scoped token allowance pools and immutable admission snapshots."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import ClassVar

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped

from app.core.config import settings
from app.core.decorators import app_label

from ..base import Base


@app_label("llm")
class LLMTokenAllowanceCycle(Base):
    """Mutable balance for one holder's anchored allowance period."""

    __tablename__ = "llm_token_allowance_cycles"
    __table_args__ = (
        UniqueConstraint(
            "plan_holder_user_id", "cycle_started_at", "allowance_pool", name="uq_llm_token_allowance_cycle_start"
        ),
        UniqueConstraint("id", "plan_holder_user_id", "allowance_pool", name="uq_llm_token_allowance_cycle_holder"),
        CheckConstraint("plan_holder_user_id > 0", name="ck_llm_token_allowance_cycle_holder"),
        CheckConstraint("length(allowance_pool) > 0", name="ck_llm_token_allowance_cycle_pool"),
        CheckConstraint("cycle_ends_at > cycle_started_at", name="ck_llm_token_allowance_cycle_window"),
        CheckConstraint("policy_version > 0", name="ck_llm_token_allowance_cycle_policy"),
        CheckConstraint("token_limit IS NULL OR token_limit >= 0", name="ck_llm_token_allowance_cycle_limit"),
        CheckConstraint("reserved_tokens >= 0 AND consumed_tokens >= 0", name="ck_llm_token_allowance_cycle_amounts"),
        CheckConstraint(
            "reserved_tokens <= 9223372036854775807 - consumed_tokens", name="ck_llm_token_allowance_cycle_sum"
        ),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    plan_holder_user_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False
    )
    allowance_pool: Mapped[str] = Column(String(48), nullable=False)
    cycle_started_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False)
    cycle_ends_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False)
    policy_version: Mapped[int] = Column(Integer, nullable=False)
    token_limit: Mapped[int | None] = Column(BigInteger, nullable=True)
    reserved_tokens: Mapped[int] = Column(BigInteger, nullable=False, server_default="0")
    consumed_tokens: Mapped[int] = Column(BigInteger, nullable=False, server_default="0")
    created_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    objects: ClassVar = None


@app_label("llm")
class LLMTokenAllowanceReservation(Base):
    """Immutable token ceiling and identity snapshot for one dispatch."""

    __tablename__ = "llm_token_allowance_reservations"
    __table_args__ = (
        UniqueConstraint("dispatch_id", name="uq_llm_token_allowance_reservations_dispatch"),
        ForeignKeyConstraint(
            ["cycle_id", "plan_holder_user_id", "allowance_pool"],
            [
                f"{settings.DB_SCHEMA}.llm_token_allowance_cycles.id",
                f"{settings.DB_SCHEMA}.llm_token_allowance_cycles.plan_holder_user_id",
                f"{settings.DB_SCHEMA}.llm_token_allowance_cycles.allowance_pool",
            ],
            name="fk_llm_token_allowance_reservation_cycle",
            ondelete="RESTRICT",
        ),
        CheckConstraint("reserved_tokens >= 0", name="ck_llm_token_allowance_reservation_amount"),
        CheckConstraint("length(allowance_pool) > 0", name="ck_llm_token_allowance_reservation_pool"),
        CheckConstraint(
            "policy_version > 0 AND credential_version >= 0", name="ck_llm_token_allowance_reservation_versions"
        ),
        CheckConstraint("funding_class IN ('platform', 'personal')", name="ck_llm_token_allowance_reservation_funding"),
        CheckConstraint(
            "consent_version IS NULL OR consent_version > 0", name="ck_llm_token_allowance_reservation_consent"
        ),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    dispatch_id: Mapped[str] = Column(
        String(32),
        ForeignKey(f"{settings.DB_SCHEMA}.llm_dispatch_receipts.dispatch_id", ondelete="RESTRICT"),
        nullable=False,
    )
    tenant_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.tenants.id", ondelete="RESTRICT"), nullable=False
    )
    actor_user_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False
    )
    plan_holder_user_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False
    )
    allowance_pool: Mapped[str] = Column(String(48), nullable=False)
    cycle_id: Mapped[int] = Column(Integer, nullable=False)
    model_row_id: Mapped[int] = Column(Integer, nullable=False)
    provider_row_id: Mapped[int] = Column(Integer, nullable=False)
    model_id: Mapped[str] = Column(String(200), nullable=False)
    catalog_id: Mapped[str] = Column(String(80), nullable=False)
    funding_class: Mapped[str] = Column(String(16), nullable=False)
    payer_ref: Mapped[str] = Column(String(200), nullable=False)
    credential_version: Mapped[int] = Column(BigInteger, nullable=False)
    consent_version: Mapped[int | None] = Column(Integer, nullable=True)
    policy_version: Mapped[int] = Column(Integer, nullable=False)
    reserved_tokens: Mapped[int] = Column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    objects: ClassVar = None


@app_label("llm")
class LLMDispatchReservationState(Base):
    """Mutable P21-T16 settlement/reconciliation state, separate from evidence."""

    __tablename__ = "llm_dispatch_reservation_states"
    __table_args__ = (
        UniqueConstraint("dispatch_id", name="uq_llm_dispatch_reservation_state_dispatch"),
        CheckConstraint(
            "status IN ('reserved', 'dispatched', 'not_dispatched', 'settled', 'unresolved')",
            name="ck_llm_dispatch_reservation_state_status",
        ),
        CheckConstraint("actual_tokens IS NULL OR actual_tokens >= 0", name="ck_llm_dispatch_reservation_state_tokens"),
        CheckConstraint(
            "actual_usd IS NULL OR (actual_usd >= 0 AND actual_usd <= 999999999.999999999)",
            name="ck_llm_dispatch_reservation_state_usd",
        ),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    dispatch_id: Mapped[str] = Column(
        String(32),
        ForeignKey(f"{settings.DB_SCHEMA}.llm_dispatch_receipts.dispatch_id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = Column(String(20), nullable=False, server_default="reserved")
    lease_until: Mapped[datetime | None] = Column(DateTime(timezone=True), nullable=True)
    actual_tokens: Mapped[int | None] = Column(BigInteger, nullable=True)
    actual_usd: Mapped[Decimal | None] = Column(Numeric(18, 9), nullable=True)
    latest_event_key: Mapped[str | None] = Column(String(64), nullable=True)
    updated_at: Mapped[datetime] = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    objects: ClassVar = None


@app_label("llm")
class LLMDispatchReservationEvent(Base):
    """Append-only settlement facts; token use and USD cost remain separate units."""

    __tablename__ = "llm_dispatch_reservation_events"
    __table_args__ = (
        UniqueConstraint("dispatch_id", "event_key", name="uq_llm_dispatch_reservation_event_key"),
        CheckConstraint(
            "event_kind IN ('admitted', 'dispatched', 'not_dispatched', 'settled', 'unresolved', 'reconciled')",
            name="ck_llm_dispatch_reservation_event_kind",
        ),
        CheckConstraint("actual_tokens IS NULL OR actual_tokens >= 0", name="ck_llm_dispatch_reservation_event_tokens"),
        CheckConstraint(
            "actual_usd IS NULL OR (actual_usd >= 0 AND actual_usd <= 999999999.999999999)",
            name="ck_llm_dispatch_reservation_event_usd",
        ),
        CheckConstraint(
            "evidence_fingerprint IS NULL OR evidence_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_llm_dispatch_reservation_event_fingerprint",
        ),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    dispatch_id: Mapped[str] = Column(
        String(32),
        ForeignKey(f"{settings.DB_SCHEMA}.llm_dispatch_receipts.dispatch_id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_key: Mapped[str] = Column(String(64), nullable=False)
    event_kind: Mapped[str] = Column(String(20), nullable=False)
    actual_tokens: Mapped[int | None] = Column(BigInteger, nullable=True)
    actual_usd: Mapped[Decimal | None] = Column(Numeric(18, 9), nullable=True)
    evidence_fingerprint: Mapped[str | None] = Column(String(64), nullable=True)
    observed_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    objects: ClassVar = None
