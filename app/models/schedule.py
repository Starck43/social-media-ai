from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import JSON, Boolean, Column, DateTime, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped

from ..core.config import settings
from ..core.decorators import app_label
from .base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from .managers.schedule_manager import ScheduleManager


@app_label("social")
class Schedule(Base, TenantScopedMixin, TimestampMixin):
    """Cron-based schedule that enqueues background jobs (M1: collect/prune/digest stubs)."""

    __tablename__ = "schedules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_schedule_tenant_name"),
        Index("ix_schedules_tenant_id", "tenant_id"),
        Index("idx_schedules_next_run_at", "next_run_at"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    name: Mapped[str] = Column(String(100), nullable=False)

    cron_expr: Mapped[str] = Column(String(100), nullable=False)  # 5-field cron expression
    timezone: Mapped[str] = Column(
        String(64), nullable=False, default=settings.SCHEDULER_TIMEZONE, server_default="Europe/Moscow"
    )
    # Job type to enqueue: 'collect' | 'digest' | 'prune'
    job_type: Mapped[str] = Column(String(20), nullable=False)
    # Job payload: {"source_ids": [...], "period": "week", "channel": "telegram", ...}
    payload: Mapped[dict[str, Any]] = Column(JSON, default=dict, nullable=False, server_default=text("'{}'::json"))
    is_active: Mapped[bool] = Column(Boolean, default=True, nullable=False, server_default="true")
    next_run_at: Mapped[datetime | None] = Column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = Column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = Column(String(20), nullable=True)  # ok | failed | skipped
    last_error: Mapped[str] = Column(Text, nullable=True)

    if TYPE_CHECKING:
        from .managers.base_manager import BaseManager

        objects: ClassVar[ScheduleManager | BaseManager]
    else:
        objects: ClassVar = None

    def __str__(self) -> str:
        return f"{self.name} ({self.cron_expr})"


from .managers.schedule_manager import ScheduleManager  # noqa: E402

Schedule.objects = ScheduleManager()
