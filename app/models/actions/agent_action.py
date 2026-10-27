from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

import sqlalchemy as sa
from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, relationship

from ...core.config import settings
from ...core.decorators import app_label
from ...types import AgentActionType, BotActionStatus
from ..base import Base, TenantScopedMixin, TimestampMixin


@app_label("scheduling")
class BotAction(Base, TenantScopedMixin, TimestampMixin):
    """Capability-neutral proposal ledger; legacy table/name retained for compatibility.

    Live execution is disabled. Source/scenario are optional origin references,
    never the owner or approval authority. Approved payloads are sealed by hash.
    """

    __tablename__ = "bot_actions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_bot_actions_tenant_idempotency"),
        Index("ix_bot_actions_tenant_id", "tenant_id"),
        Index("ix_bot_actions_status", "status"),
        Index("ix_bot_actions_agent_scenario_id", "agent_scenario_id"),
        Index("ix_bot_actions_agent_task_id", "agent_task_id"),
        Index("ix_bot_actions_source_id", "source_id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)

    agent_scenario_id: Mapped[int | None] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.agent_scenarios.id", ondelete="SET NULL"), nullable=True
    )
    # The task that produced this action. Rate limiting and cooldown are counted
    # per task, not per scenario: the guards live on the task now, and one
    # scenario can be shared by several tasks whose limits must not merge.
    # Nullable because an action may be created outside a task run (a manual
    # agent invocation), in which case there is nothing to count against.
    agent_task_id: Mapped[int | None] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.agent_tasks.id", ondelete="SET NULL"), nullable=True
    )
    source_id: Mapped[int | None] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.sources.id", ondelete="SET NULL"), nullable=True
    )
    analytics_id: Mapped[int | None] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.ai_analytics.id", ondelete="SET NULL"), nullable=True
    )

    action_type: Mapped[AgentActionType | None] = AgentActionType.sa_column(
        type_name="bot_action_type", nullable=True, store_as_name=True
    )
    status: Mapped[BotActionStatus] = BotActionStatus.sa_column(
        type_name="bot_action_status",
        nullable=False,
        default=BotActionStatus.PENDING,
        store_as_name=True,
        server_default="PENDING",
    )

    capability: Mapped[str] = Column(
        String(100), nullable=False, default="social.legacy", server_default="social.legacy"
    )
    origin: Mapped[str] = Column(String(20), nullable=False, default="task", server_default="task")
    target_ref: Mapped[str | None] = Column(String(300), nullable=True)
    idempotency_key: Mapped[str | None] = Column(String(200), nullable=True)
    payload_hash: Mapped[str | None] = Column(String(64), nullable=True)
    approved_payload_hash: Mapped[str | None] = Column(String(64), nullable=True)
    approved_user_id: Mapped[int | None] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="SET NULL")
    )
    approval_expires_at: Mapped[datetime | None] = Column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = Column(DateTime(timezone=True))
    claim_expires_at: Mapped[datetime | None] = Column(DateTime(timezone=True))
    claim_token_hash: Mapped[str | None] = Column(String(64))
    executed_at: Mapped[datetime | None] = Column(DateTime(timezone=True))
    history: Mapped[list[dict[str, Any]]] = Column(
        JSON, nullable=False, default=list, server_default=sa.text("'[]'::json")
    )
    # Snapshot persists even when nullable origin FKs are removed.
    origin_snapshot: Mapped[dict[str, Any] | None] = Column(JSON, nullable=True)

    payload: Mapped[dict[str, Any]] = Column(JSON, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    result: Mapped[dict[str, Any] | None] = Column(JSON, nullable=True)
    error: Mapped[str | None] = Column(Text, nullable=True)

    dry_run: Mapped[bool] = Column(Boolean, nullable=False, default=True, server_default=sa.text("true"))
    confirmed_by: Mapped[int | None] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.tenant_users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = Column(DateTime(timezone=True), nullable=True)

    attempts: Mapped[int] = Column(Integer, nullable=False, default=0, server_default=sa.text("0"))

    scenario = relationship("AgentScenario", foreign_keys=[agent_scenario_id])
    task = relationship("AgentTask", foreign_keys=[agent_task_id])
    source = relationship("Source", foreign_keys=[source_id])
    analytics = relationship("AIAnalytics", foreign_keys=[analytics_id])

    if TYPE_CHECKING:
        from ..managers.base_manager import BaseManager
        from ..managers.bot_action_manager import BotActionManager

        objects: ClassVar[BotActionManager | BaseManager]
    else:
        objects: ClassVar = None

    def __str__(self) -> str:
        return f"BotAction#{self.id} ({self.capability}, {self.status.name})"


# Both names identify the same mapper/table; never subclass to create an alias.
AgentAction = BotAction
