from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import JSON, Boolean, Column, DateTime, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, relationship

from ..core.config import settings
from ..core.decorators import app_label
from .base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from .managers.agent_task_manager import AgentTaskManager
    from .tenant import Tenant


@app_label("social")
class AgentTask(Base, TenantScopedMixin, TimestampMixin):
    """Cron-based task that enqueues background jobs (collect/prune/digest/analyze/learn/reflect)."""

    __tablename__ = "agent_tasks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_agent_task_tenant_name"),
        Index("ix_agent_tasks_tenant_id", "tenant_id"),
        Index("idx_agent_tasks_next_run_at", "next_run_at"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    name: Mapped[str] = Column(String(100), nullable=False)

    cron_expr: Mapped[str] = Column(String(100), nullable=False)  # 5-field cron expression
    timezone: Mapped[str] = Column(
        String(64), nullable=False, default=settings.SCHEDULER_TIMEZONE, server_default="Europe/Moscow"
    )
    # Job type to enqueue: 'collect' | 'digest' | 'prune' | 'analyze' | 'learn' | 'reflect'
    job_type: Mapped[str] = Column(String(20), nullable=False)
    # Job payload: {"source_ids": [...], "scenario_id": 5, "period": "week", ...}
    payload: Mapped[dict[str, Any]] = Column(JSON, default=dict, nullable=False, server_default=text("'{}'::json"))
    is_active: Mapped[bool] = Column(Boolean, default=True, nullable=False, server_default="true")
    next_run_at: Mapped[datetime | None] = Column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = Column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = Column(String(20), nullable=True)  # ok | failed | skipped
    last_error: Mapped[str] = Column(Text, nullable=True)

    # Owning workspace (tenant); friendly name shown in admin list/form instead of raw ID
    tenant: Mapped["Tenant"] = relationship("Tenant")

    if TYPE_CHECKING:
        from .managers.base_manager import BaseManager

        objects: ClassVar[AgentTaskManager | BaseManager]
    else:
        objects: ClassVar = None

    def __str__(self) -> str:
        return f"{self.name} ({self.cron_expr})"


from .managers.agent_task_manager import AgentTaskManager  # noqa: E402

AgentTask.objects = AgentTaskManager()
