"""Shared cross-process LLM provider rate buckets (P21-T14, dormant).

One row is one atomic token-bucket scope: a stable opaque pool identity, a scope
discriminator, a scope key, a unit and an optional model-family key. The token
bucket algorithm and `blocked_until`/Retry-After floor semantics are reused from
the social `provider_rate_budgets` store; its rows are **not** reused — those are
tenant/connection scoped (`workspace_connections` FK) and cannot name a shared LLM
quota pool.

Scope keys are opaque and never carry a secret or a credential version:

- `upstream` — `pool:<pool-id>`. The pool id is deployment-assigned
  (`LLM_RATE_POOLS` maps a credential ref to its pool; refs sharing an upstream
  account map to the same pool). Rotation/version is never part of the identity,
  so a credential-version change never refills the pool. Personal BYOK pools are
  `pool:personal:<credential_owner_user_id>`.
- `holder` — `holder:<plan_holder_user_id>`: the governing tariff/payer stays the
  host owner, including invited members (the actor's own plan never widens it).
- `actor` — `actor:<actor_user_id>`: actual-actor fairness.
- `workspace` — `workspace:<tenant_id>`: workspace fairness.

Units are `request` (RPM) and `token` (TPM). Tariff tokens are the P2-T17/P21-T15
owner allowance, never upstream TPM; the token bucket here is backpressure, not
billing. `model_family` NULL means "all families in this scope"; the unique index
uses `COALESCE(model_family, '')` so NULL can never admit duplicate buckets.

`blocked_until` is a Retry-After floor: refill earns nothing while blocked, and a
huge hint is never shortened. `available_units` is spent at admission and never
refunded for a possibly sent request. Global table (no tenant mixin): the upstream
pool is shared across tenants by design.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Index, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped

from ...core.config import settings
from ...core.decorators import app_label
from ..base import Base

#: Scope discriminators. `upstream` is the provider quota pool; the rest are
#: fairness buckets, never the upstream pool.
RATE_SCOPES: tuple[str, ...] = ("upstream", "holder", "actor", "workspace")

#: Bucket units: requests per minute and tokens per minute.
RATE_UNITS: tuple[str, ...] = ("request", "token")


@app_label("llm")
class LLMRateBucket(Base):
    """One atomic token-bucket scope for LLM dispatch backpressure."""

    __tablename__ = "llm_rate_buckets"
    __table_args__ = (
        CheckConstraint("scope IN ('upstream', 'holder', 'actor', 'workspace')", name="ck_llm_rate_buckets_scope"),
        CheckConstraint("unit IN ('request', 'token')", name="ck_llm_rate_buckets_unit"),
        CheckConstraint("length(scope_key) > 0", name="ck_llm_rate_buckets_scope_key"),
        CheckConstraint("capacity_units > 0", name="ck_llm_rate_buckets_capacity"),
        CheckConstraint("refill_units_per_second > 0", name="ck_llm_rate_buckets_refill"),
        CheckConstraint("available_units >= 0", name="ck_llm_rate_buckets_available"),
        CheckConstraint("available_units <= capacity_units", name="ck_llm_rate_buckets_available_bounded"),
        CheckConstraint("model_family IS NULL OR length(model_family) > 0", name="ck_llm_rate_buckets_family"),
        # NULL family must not admit duplicates: the expression folds NULL to ''.
        Index(
            "uq_llm_rate_buckets_scope",
            "scope",
            "scope_key",
            "unit",
            text("(COALESCE(model_family, ''))"),
            unique=True,
        ),
        Index("ix_llm_rate_buckets_lookup", "scope", "scope_key", "unit"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    scope: Mapped[str] = Column(String(16), nullable=False)
    scope_key: Mapped[str] = Column(String(200), nullable=False)
    unit: Mapped[str] = Column(String(16), nullable=False)
    model_family: Mapped[str | None] = Column(String(64), nullable=True)
    capacity_units: Mapped[int] = Column(BigInteger, nullable=False)
    refill_units_per_second: Mapped[Decimal] = Column(Numeric(18, 6), nullable=False)
    available_units: Mapped[Decimal] = Column(Numeric(18, 6), nullable=False)
    updated_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False)
    blocked_until: Mapped[datetime | None] = Column(DateTime(timezone=True), nullable=True)

    if TYPE_CHECKING:
        from ..managers.base_manager import BaseManager

        objects: ClassVar[BaseManager]
    else:
        objects: ClassVar = None
