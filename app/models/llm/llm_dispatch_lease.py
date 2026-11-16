"""Dispatch-bound concurrency leases for LLM admission (P21-T14, dormant).

A token bucket alone cannot recover crashed in-flight slots, so every admitted
attempt also holds one lease row. The lease is an **admission** lease: it bounds
concurrency and is reclaimable after its aware expiry, but it is never the
financial reservation expiry (P21-T15) and its expiry never pretends an upstream
call stopped — reclaim only frees the concurrency slot, it never refunds bucket
units and never releases token/USD liability.

`lease_id` is the dispatch identity (`DispatchRequest.dispatch_id`, 32 lowercase
hex): one lease per attempt, bound to that attempt. `released_at` is set on
release; NULL means in-flight. The bounded reclaim query is
`WHERE released_at IS NULL AND expires_at <= now ORDER BY expires_at LIMIT n`,
served by the partial index below. Global table (no tenant mixin): the upstream
concurrency pool is shared across tenants by design.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import CheckConstraint, Column, DateTime, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped

from ...core.config import settings
from ...core.decorators import app_label
from ..base import Base


@app_label("llm")
class LLMDispatchLease(Base):
    """One in-flight admission slot, bound to one dispatch identity."""

    __tablename__ = "llm_dispatch_leases"
    __table_args__ = (
        UniqueConstraint("lease_id", name="uq_llm_dispatch_leases_identity"),
        CheckConstraint("lease_id ~ '^[0-9a-f]{32}$'", name="ck_llm_dispatch_leases_identity"),
        CheckConstraint("scope IN ('upstream', 'holder', 'actor', 'workspace')", name="ck_llm_dispatch_leases_scope"),
        CheckConstraint("length(scope_key) > 0", name="ck_llm_dispatch_leases_scope_key"),
        CheckConstraint("model_family IS NULL OR length(model_family) > 0", name="ck_llm_dispatch_leases_family"),
        Index("ix_llm_dispatch_leases_scope", "scope", "scope_key", "model_family", "expires_at"),
        Index(
            "ix_llm_dispatch_leases_reclaim",
            "expires_at",
            postgresql_where=text("released_at IS NULL"),
        ),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    lease_id: Mapped[str] = Column(String(32), nullable=False)
    scope: Mapped[str] = Column(String(16), nullable=False)
    scope_key: Mapped[str] = Column(String(200), nullable=False)
    model_family: Mapped[str | None] = Column(String(64), nullable=True)
    expires_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = Column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    if TYPE_CHECKING:
        from ..managers.base_manager import BaseManager

        objects: ClassVar[BaseManager]
    else:
        objects: ClassVar = None
