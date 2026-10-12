"""Immutable journal of actual LLM provider attempts (PRD-03 accounting evidence)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Column, Date, DateTime, Index, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped

from ...core.config import settings
from ...core.decorators import app_label
from ..base import Base, TenantScopedMixin


@app_label("ai")
class LLMAttempt(Base, TenantScopedMixin):
    """One row per dispatched provider attempt; rows are never updated in place.

    Unlike `AIAnalytics`, `jobs.llm_cost` or `digest_runs.llm_cost` — mutable
    projections that overwrite, re-price or can be reaped — this table is the
    accounting evidence itself. Execution references (`source_id`, `job_id`,
    `agent_task_id`) are deliberately plain integers, not foreign keys: queue
    and transcript cleanup must not destroy spend evidence.

    Money is exact Decimal USD at 1e-9 precision. A NULL cost means "not
    computable" (unknown tariff or unknown usage) — never silently zero, and
    missing usage is unknown usage, not zero usage.
    """

    __tablename__ = "llm_attempts"
    __table_args__ = (
        UniqueConstraint("attempt_key", name="uq_llm_attempts_key"),
        Index("ix_llm_attempts_tenant_id", "tenant_id"),
        Index("idx_llm_attempts_source_date", "source_id", "analysis_date"),
        Index("idx_llm_attempts_job", "job_id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    attempt_key: Mapped[str] = Column(
        String(64),
        nullable=False,
        comment="SHA256 of the attempt's content identity; repeat writes are idempotent",
    )
    purpose: Mapped[str] = Column(
        String(32), nullable=False, comment="'analysis' | future: 'digest' | 'learn' | 'agent_chat'"
    )
    stage: Mapped[str] = Column(
        String(32),
        nullable=False,
        comment="'text_analysis' | 'image_analysis' | 'video_analysis' | 'unified_summary'",
    )

    source_id: Mapped[int | None] = Column(Integer, nullable=True)
    analysis_date: Mapped[Date | None] = Column(Date, nullable=True)
    job_id: Mapped[int | None] = Column(Integer, nullable=True)
    agent_task_id: Mapped[int | None] = Column(Integer, nullable=True)

    llm_model_row_id: Mapped[int | None] = Column(Integer, nullable=True)
    model_id: Mapped[str | None] = Column(String(200), nullable=True)
    provider_name: Mapped[str | None] = Column(String(200), nullable=True)
    input_cost_per_1k: Mapped[Decimal | None] = Column(Numeric(18, 8), nullable=True)
    output_cost_per_1k: Mapped[Decimal | None] = Column(Numeric(18, 8), nullable=True)

    prompt_tokens: Mapped[int | None] = Column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = Column(Integer, nullable=True)

    cost_usd: Mapped[Decimal | None] = Column(Numeric(18, 9), nullable=True)
    cost_certainty: Mapped[str] = Column(
        String(32), nullable=False, comment="'priced' | 'free' | 'unknown_tariff' | 'unknown_usage' | 'not_dispatched'"
    )
    outcome: Mapped[str] = Column(
        String(32), nullable=False, comment="'succeeded' | 'failed_output' | 'failed_transport' | 'unknown'"
    )
    created_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    if TYPE_CHECKING:
        from ..managers.llm_attempt_manager import LLMAttemptManager

        objects: ClassVar[LLMAttemptManager]
    else:
        objects: ClassVar = None
