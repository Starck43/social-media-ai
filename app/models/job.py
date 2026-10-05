from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped

from ..core.config import settings
from ..core.decorators import app_label
from .base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from .managers.job_manager import JobManager


@app_label("social")
class Job(Base, TenantScopedMixin, TimestampMixin):
    """Queued background job claimed by the worker (DB-backed queue, no Redis)."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_tenant_id", "tenant_id"),
        Index("idx_jobs_status_run_at", "status", "run_at"),
        Index("idx_jobs_agent_task_id", "agent_task_id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    agent_task_id: Mapped[int | None] = Column(
        Integer,
        ForeignKey(f"{settings.DB_SCHEMA}.agent_tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    # 'collect' | 'digest' | 'prune'
    job_type: Mapped[str] = Column(String(20), nullable=False)
    payload: Mapped[dict[str, Any]] = Column(JSON, default=dict, nullable=False, server_default=text("'{}'::json"))
    # 'pending' | 'running' | 'done' | 'failed'
    status: Mapped[str] = Column(String(20), default="pending", nullable=False, server_default="pending")
    run_at: Mapped[DateTime] = Column(DateTime(timezone=True), nullable=False)
    locked_at: Mapped[DateTime] = Column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[DateTime | None] = Column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[DateTime | None] = Column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = Column(Integer, default=0, nullable=False, server_default="0")
    max_attempts: Mapped[int] = Column(Integer, default=settings.JOB_MAX_ATTEMPTS, nullable=False, server_default="3")
    result: Mapped[dict[str, Any]] = Column(JSON, nullable=True)
    error: Mapped[str] = Column(Text, nullable=True)
    # USD spent on the LLM call for this job (NULL = none / unknown tariffs).
    # Priced from llm_models tariffs by run_learn/run_reflect via the
    # usage["cost"] block; feeds daily_cost_today().
    llm_cost: Mapped[float | None] = Column(Float, nullable=True)

    if TYPE_CHECKING:
        from .managers.base_manager import BaseManager

        objects: ClassVar[JobManager | BaseManager]
    else:
        objects: ClassVar = None

    def __str__(self) -> str:
        return f"Job#{self.id}[{self.job_type}] {self.status}"


from .managers.job_manager import JobManager  # noqa: E402

Job.objects = JobManager()
