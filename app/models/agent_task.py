from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Table, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, relationship

from ..core.config import settings
from ..core.decorators import app_label
from .base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from .agent_scenario import AgentScenario
    from .managers.agent_task_manager import AgentTaskManager
    from .source import Source
    from .tenant import Tenant


# Many-to-many between tasks and sources (replaces the old payload["source_ids"] list)
agent_task_sources = Table(
    "agent_task_sources",
    Base.metadata,
    Column("agent_task_id", Integer, ForeignKey(f"{settings.DB_SCHEMA}.agent_tasks.id", ondelete="CASCADE"), primary_key=True),
    Column("source_id", Integer, ForeignKey(f"{settings.DB_SCHEMA}.sources.id", ondelete="CASCADE"), primary_key=True),
    schema=settings.DB_SCHEMA,
)


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
    # Job type to enqueue: 'collect' | 'digest' | 'prune' | 'analyze' | 'learn' | 'reflect'
    job_type: Mapped[str] = Column(String(20), nullable=False)
    # Job payload: {"period": "week", "monitored_users": [...], "excluded_users": [...], ...}
    payload: Mapped[dict[str, Any]] = Column(JSON, default=dict, nullable=False, server_default=text("'{}'::json"))
    is_active: Mapped[bool] = Column(Boolean, default=True, nullable=False, server_default="true")
    next_run_at: Mapped[datetime | None] = Column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = Column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = Column(String(20), nullable=True)  # ok | failed | skipped
    last_error: Mapped[str] = Column(Text, nullable=True)

    # Owning workspace (tenant);
    tenant: Mapped["Tenant"] = relationship("Tenant")

    # Reusable scenario applied when this task runs (the scenario lives on the
    # task, not the source — a source may be reused across tasks with different
    # scenarios; the workspace default is the fallback for taskless runs)
    agent_scenario_id: Mapped[int | None] = Column(
        Integer,
        ForeignKey(f"{settings.DB_SCHEMA}.agent_scenarios.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_scenario: Mapped["AgentScenario | None"] = relationship(
        "AgentScenario",
        back_populates="agent_tasks",
    )

    # Sources this task operates on (many-to-many). Empty = all active sources.
    sources: Mapped[list["Source"]] = relationship(
        "Source",
        secondary=agent_task_sources,
        backref="agent_tasks",
    )

    if TYPE_CHECKING:
        from .managers.base_manager import BaseManager

        objects: ClassVar[AgentTaskManager | BaseManager]
    else:
        objects: ClassVar = None

    def __str__(self) -> str:
        return f"{self.name} ({self.cron_expr})"


from .managers.agent_task_manager import AgentTaskManager  # noqa: E402

AgentTask.objects = AgentTaskManager()
