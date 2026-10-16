from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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
from ..base import Base


@app_label("billing")
class PersonalBillingCutover(Base):
    """Reviewed clean-day boundary before personal balances become authoritative."""

    __tablename__ = "personal_billing_cutovers"
    __table_args__ = (
        UniqueConstraint("account_id", name="uq_personal_billing_cutovers_account"),
        CheckConstraint(
            "effective_at = date_trunc('day', effective_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'",
            name="ck_personal_billing_cutovers_utc_midnight",
        ),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    account_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.personal_billing_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    effective_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False)
    reviewed_by_user_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False
    )
    review_version: Mapped[str] = Column(
        String(32), nullable=False, default="clean-midnight-v1", server_default="clean-midnight-v1"
    )
    created_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


@app_label("billing")
class PersonalLLMBoundPolicy(Base):
    """Internal reviewed per-provider/model request envelope; no generic estimator."""

    __tablename__ = "personal_llm_bound_policies"
    __table_args__ = (
        UniqueConstraint("provider_row_id", "model_row_id", "version", name="uq_personal_llm_bound_policy_version"),
        CheckConstraint("max_input_tokens > 0 AND max_output_tokens > 0", name="ck_personal_llm_bound_policy_tokens"),
        CheckConstraint("request_mode IN ('text_only', 'text_and_tools')", name="ck_personal_llm_bound_policy_mode"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    provider_row_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.llm_providers.id", ondelete="RESTRICT"), nullable=False
    )
    model_row_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.llm_models.id", ondelete="RESTRICT"), nullable=False
    )
    provider_name: Mapped[str] = Column(String(200), nullable=False)
    model_id: Mapped[str] = Column(String(200), nullable=False)
    version: Mapped[str] = Column(String(64), nullable=False)
    max_input_tokens: Mapped[int] = Column(Integer, nullable=False)
    max_output_tokens: Mapped[int] = Column(Integer, nullable=False)
    request_mode: Mapped[str] = Column(String(20), nullable=False)
    reviewed_by_user_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


@app_label("billing")
class PersonalLLMBudgetDay(Base):
    """Mutable per-account/day balances derived from the immutable ledger."""

    __tablename__ = "personal_llm_budget_days"
    __table_args__ = (
        UniqueConstraint("account_id", "utc_day", name="uq_personal_llm_budget_day_account_day"),
        CheckConstraint(
            "settled_usd >= 0 AND settled_usd <= 999999999.999999999 AND reserved_usd >= 0 AND "
            "reserved_usd <= 999999999.999999999",
            name="ck_personal_llm_budget_day_amounts",
        ),
        CheckConstraint(
            "undefined_count >= 0 AND unbounded_count >= 0 AND unresolved_count >= 0",
            name="ck_personal_llm_budget_day_counts",
        ),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    account_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.personal_billing_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    utc_day: Mapped[date] = Column(Date, nullable=False)
    settled_usd: Mapped[Decimal] = Column(Numeric(18, 9), nullable=False, server_default="0")
    reserved_usd: Mapped[Decimal] = Column(Numeric(18, 9), nullable=False, server_default="0")
    undefined_count: Mapped[int] = Column(Integer, nullable=False, server_default="0")
    unbounded_count: Mapped[int] = Column(Integer, nullable=False, server_default="0")
    unresolved_count: Mapped[int] = Column(Integer, nullable=False, server_default="0")
    frozen: Mapped[bool] = Column(Boolean, nullable=False, default=False, server_default="false")
    baseline_version: Mapped[str] = Column(String(64), nullable=False)
    created_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    if TYPE_CHECKING:
        from ..managers.base_manager import BaseManager

        objects: ClassVar[BaseManager]
    else:
        objects: ClassVar = None


@app_label("billing")
class PersonalLLMReservation(Base):
    """Immutable ceiling and cap snapshot for one personal dispatch."""

    __tablename__ = "personal_llm_reservations"
    __table_args__ = (
        UniqueConstraint("dispatch_id", name="uq_personal_llm_reservations_dispatch"),
        UniqueConstraint(
            "dispatch_id", "account_id", "utc_day", name="uq_personal_llm_reservations_dispatch_account_day"
        ),
        ForeignKeyConstraint(
            ["account_id", "utc_day"],
            [
                f"{settings.DB_SCHEMA}.personal_llm_budget_days.account_id",
                f"{settings.DB_SCHEMA}.personal_llm_budget_days.utc_day",
            ],
            ondelete="RESTRICT",
            name="fk_personal_llm_reservations_day",
        ),
        CheckConstraint(
            "ceiling_state IN ('bounded', 'unbounded', 'undefined')", name="ck_personal_llm_reservations_state"
        ),
        CheckConstraint(
            "(ceiling_usd IS NULL OR (ceiling_usd >= 0 AND ceiling_usd <= 999999999.999999999)) AND "
            "(cap_usd IS NULL OR (cap_usd > 0 AND cap_usd <= 999999999.999999999))",
            name="ck_personal_llm_reservations_amounts",
        ),
        CheckConstraint(
            "(ceiling_state = 'bounded' AND ceiling_usd IS NOT NULL AND "
            "(ceiling_usd = 0 OR bound_version IS NOT NULL)) OR "
            "(ceiling_state = 'unbounded' AND ceiling_usd IS NULL AND bound_version IS NULL AND cap_usd IS NULL) OR "
            "(ceiling_state = 'undefined' AND ceiling_usd = 0 AND bound_version IS NULL)",
            name="ck_personal_llm_reservations_bound",
        ),
        CheckConstraint(
            "undefined_policy_version = 'owner-undefined-allow-v1'",
            name="ck_personal_llm_reservations_undefined_policy",
        ),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    dispatch_id: Mapped[str] = Column(
        String(32),
        ForeignKey(f"{settings.DB_SCHEMA}.personal_llm_dispatches.dispatch_id", ondelete="RESTRICT"),
        nullable=False,
    )
    account_id: Mapped[int] = Column(Integer, nullable=False)
    utc_day: Mapped[date] = Column(Date, nullable=False)
    ceiling_state: Mapped[str] = Column(String(16), nullable=False)
    ceiling_usd: Mapped[Decimal | None] = Column(Numeric(18, 9), nullable=True)
    bound_version: Mapped[str | None] = Column(String(64), nullable=True)
    undefined_policy_version: Mapped[str] = Column(String(32), nullable=False)
    cap_usd: Mapped[Decimal | None] = Column(Numeric(18, 9), nullable=True)
    created_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    if TYPE_CHECKING:
        from ..managers.base_manager import BaseManager

        objects: ClassVar[BaseManager]
    else:
        objects: ClassVar = None


@app_label("billing")
class PersonalLLMReservationState(Base):
    """Mutable idempotency projection for a reservation's current liability."""

    __tablename__ = "personal_llm_reservation_states"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dispatch_id", "account_id", "utc_day"],
            [
                f"{settings.DB_SCHEMA}.personal_llm_reservations.dispatch_id",
                f"{settings.DB_SCHEMA}.personal_llm_reservations.account_id",
                f"{settings.DB_SCHEMA}.personal_llm_reservations.utc_day",
            ],
            ondelete="RESTRICT",
            name="fk_personal_llm_reservation_states_reservation",
        ),
        ForeignKeyConstraint(
            ["account_id", "utc_day"],
            [
                f"{settings.DB_SCHEMA}.personal_llm_budget_days.account_id",
                f"{settings.DB_SCHEMA}.personal_llm_budget_days.utc_day",
            ],
            ondelete="RESTRICT",
            name="fk_personal_llm_reservation_states_day",
        ),
        CheckConstraint(
            "settled_usd >= 0 AND settled_usd <= 999999999.999999999 AND reserved_usd >= 0 AND "
            "reserved_usd <= 999999999.999999999",
            name="ck_personal_llm_reservation_states_amounts",
        ),
        CheckConstraint(
            "status IN ('reserved', 'settled', 'released')", name="ck_personal_llm_reservation_states_status"
        ),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    dispatch_id: Mapped[str] = Column(
        String(32),
        ForeignKey(f"{settings.DB_SCHEMA}.personal_llm_reservations.dispatch_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    account_id: Mapped[int] = Column(Integer, nullable=False)
    utc_day: Mapped[date] = Column(Date, nullable=False)
    status: Mapped[str] = Column(String(16), nullable=False)
    reserved_usd: Mapped[Decimal] = Column(Numeric(18, 9), nullable=False)
    settled_usd: Mapped[Decimal] = Column(Numeric(18, 9), nullable=False)
    latest_observed_at: Mapped[datetime | None] = Column(DateTime(timezone=True), nullable=True)
    latest_event_id: Mapped[str | None] = Column(
        String(32), ForeignKey(f"{settings.DB_SCHEMA}.llm_outcome_events.event_id", ondelete="RESTRICT"), nullable=True
    )
    updated_at: Mapped[datetime] = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    if TYPE_CHECKING:
        from ..managers.base_manager import BaseManager

        objects: ClassVar[BaseManager]
    else:
        objects: ClassVar = None


@app_label("billing")
class PersonalLLMLedgerEntry(Base):
    """Immutable accounting delta for a reservation or stored outcome event."""

    __tablename__ = "personal_llm_ledger_entries"
    __table_args__ = (
        UniqueConstraint("entry_key", name="uq_personal_llm_ledger_entry_key"),
        UniqueConstraint("event_id", "entry_kind", name="uq_personal_llm_ledger_event_kind"),
        ForeignKeyConstraint(
            ["account_id", "utc_day"],
            [
                f"{settings.DB_SCHEMA}.personal_llm_budget_days.account_id",
                f"{settings.DB_SCHEMA}.personal_llm_budget_days.utc_day",
            ],
            ondelete="RESTRICT",
            name="fk_personal_llm_ledger_entries_day",
        ),
        CheckConstraint("entry_key ~ '^[0-9a-f]{32}$'", name="ck_personal_llm_ledger_entry_key"),
        CheckConstraint(
            "entry_kind IN ('reserve', 'observation', 'settlement', 'release')",
            name="ck_personal_llm_ledger_entry_kind",
        ),
        CheckConstraint(
            "reserved_delta_usd BETWEEN -999999999.999999999 AND 999999999.999999999 AND "
            "settled_delta_usd BETWEEN -999999999.999999999 AND 999999999.999999999",
            name="ck_personal_llm_ledger_entry_deltas",
        ),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    entry_key: Mapped[str] = Column(String(32), nullable=False)
    dispatch_id: Mapped[str] = Column(
        String(32),
        ForeignKey(f"{settings.DB_SCHEMA}.personal_llm_reservations.dispatch_id", ondelete="RESTRICT"),
        nullable=False,
    )
    account_id: Mapped[int] = Column(Integer, nullable=False)
    utc_day: Mapped[date] = Column(Date, nullable=False)
    event_id: Mapped[str | None] = Column(
        String(32), ForeignKey(f"{settings.DB_SCHEMA}.llm_outcome_events.event_id", ondelete="RESTRICT"), nullable=True
    )
    entry_kind: Mapped[str] = Column(String(16), nullable=False)
    reserved_delta_usd: Mapped[Decimal] = Column(Numeric(18, 9), nullable=False)
    settled_delta_usd: Mapped[Decimal] = Column(Numeric(18, 9), nullable=False)
    created_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    if TYPE_CHECKING:
        from ..managers.base_manager import BaseManager

        objects: ClassVar[BaseManager]
    else:
        objects: ClassVar = None


@app_label("billing")
class PersonalLLMBillingIncident(Base):
    """Immutable evidence that actual billing exceeded a reviewed admission bound."""

    __tablename__ = "personal_llm_billing_incidents"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_personal_llm_billing_incident_event"),
        CheckConstraint(
            "incident_kind IN ('bound_breach', 'cap_overrun', 'bound_and_cap_overrun', 'terminal_conflict')",
            name="ck_personal_llm_billing_incident_kind",
        ),
        CheckConstraint(
            "(actual_usd IS NULL OR (actual_usd >= 0 AND actual_usd <= 999999999.999999999)) AND "
            "reserved_usd >= 0 AND reserved_usd <= 999999999.999999999",
            name="ck_personal_llm_billing_incident_amounts",
        ),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    event_id: Mapped[str] = Column(
        String(32), ForeignKey(f"{settings.DB_SCHEMA}.llm_outcome_events.event_id", ondelete="RESTRICT"), nullable=False
    )
    dispatch_id: Mapped[str] = Column(
        String(32),
        ForeignKey(f"{settings.DB_SCHEMA}.personal_llm_reservations.dispatch_id", ondelete="RESTRICT"),
        nullable=False,
    )
    account_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.personal_billing_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    utc_day: Mapped[date] = Column(Date, nullable=False)
    incident_kind: Mapped[str] = Column(String(32), nullable=False)
    reserved_usd: Mapped[Decimal] = Column(Numeric(18, 9), nullable=False)
    actual_usd: Mapped[Decimal | None] = Column(Numeric(18, 9), nullable=True)
    created_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


@app_label("billing")
class PersonalLegacyAttributionConsent(Base):
    """Account-owner consent for one exact immutable legacy identity."""

    __tablename__ = "personal_legacy_attribution_consents"
    __table_args__ = (
        CheckConstraint(
            "(dispatch_id IS NOT NULL) <> (attempt_key IS NOT NULL)", name="ck_personal_legacy_consent_one_identity"
        ),
        UniqueConstraint("source_key", "account_id", name="uq_personal_legacy_consent_identity_account"),
        CheckConstraint(
            "(dispatch_id IS NOT NULL AND source_key = 'r:' || dispatch_id) OR "
            "(attempt_key IS NOT NULL AND source_key = 'a:' || attempt_key)",
            name="ck_personal_legacy_consent_source_key",
        ),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    source_key: Mapped[str] = Column(String(64), nullable=False)
    dispatch_id: Mapped[str | None] = Column(
        String(32),
        ForeignKey(f"{settings.DB_SCHEMA}.llm_dispatch_receipts.dispatch_id", ondelete="RESTRICT"),
        nullable=True,
    )
    attempt_key: Mapped[str | None] = Column(
        String(64), ForeignKey(f"{settings.DB_SCHEMA}.llm_attempts.attempt_key", ondelete="RESTRICT"), nullable=True
    )
    tenant_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.tenants.id", ondelete="RESTRICT"), nullable=False
    )
    account_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.personal_billing_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    consented_by_user_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False
    )
    consented_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


@app_label("billing")
class PersonalLegacyAttribution(Base):
    """Reviewed reporting map. It never writes personal ledger or changes history."""

    __tablename__ = "personal_legacy_attributions"
    __table_args__ = (
        CheckConstraint(
            "(dispatch_id IS NOT NULL) <> (attempt_key IS NOT NULL)", name="ck_personal_legacy_attribution_one_identity"
        ),
        UniqueConstraint("source_key", name="uq_personal_legacy_attribution_identity"),
        UniqueConstraint("consent_id", name="uq_personal_legacy_attribution_consent"),
        CheckConstraint(
            "(dispatch_id IS NOT NULL AND source_key = 'r:' || dispatch_id) OR "
            "(attempt_key IS NOT NULL AND source_key = 'a:' || attempt_key)",
            name="ck_personal_legacy_attribution_source_key",
        ),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    source_key: Mapped[str] = Column(String(64), nullable=False)
    dispatch_id: Mapped[str | None] = Column(
        String(32),
        ForeignKey(f"{settings.DB_SCHEMA}.llm_dispatch_receipts.dispatch_id", ondelete="RESTRICT"),
        nullable=True,
    )
    attempt_key: Mapped[str | None] = Column(
        String(64), ForeignKey(f"{settings.DB_SCHEMA}.llm_attempts.attempt_key", ondelete="RESTRICT"), nullable=True
    )
    tenant_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.tenants.id", ondelete="RESTRICT"), nullable=False
    )
    account_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.personal_billing_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    consent_id: Mapped[int] = Column(
        Integer,
        ForeignKey(f"{settings.DB_SCHEMA}.personal_legacy_attribution_consents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    consented_by_user_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False
    )
    reviewed_by_user_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False
    )
    reviewed_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
