from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..core.config import settings
from ..core.decorators import app_label
from . import Base, TimestampMixin

if TYPE_CHECKING:
    from .managers.job_manager import JobManager


@app_label("social")
class Job(Base, TimestampMixin):
    """Queued background job claimed by the worker (DB-backed queue, no Redis)."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("idx_jobs_status_run_at", "status", "run_at"),
        Index("idx_jobs_schedule_id", "schedule_id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    schedule_id: Mapped[int | None] = Column(
        Integer,
        ForeignKey("social_manager.schedules.id", ondelete="SET NULL"),
        nullable=True,
    )
    # 'collect' | 'digest' | 'prune'
    job_type: Mapped[str] = Column(String(20), nullable=False)
    payload: Mapped[dict[str, Any]] = Column(JSON, default=dict, nullable=False)
    # 'pending' | 'running' | 'done' | 'failed'
    status: Mapped[str] = Column(String(20), default="pending", nullable=False)
    run_at: Mapped[DateTime] = Column(DateTime(timezone=True), nullable=False)
    locked_at: Mapped[DateTime] = Column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = Column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = Column(Integer, default=settings.JOB_MAX_ATTEMPTS, nullable=False)
    result: Mapped[dict[str, Any]] = Column(JSON, nullable=True)
    error: Mapped[str] = Column(Text, nullable=True)

    if TYPE_CHECKING:
        from .managers.base_manager import BaseManager

        objects: ClassVar[JobManager | BaseManager]
    else:
        objects: ClassVar = None

    def __str__(self) -> str:
        return f"Job#{self.id}[{self.job_type}] {self.status}"


from .managers.job_manager import JobManager  # noqa: E402

Job.objects = JobManager()
