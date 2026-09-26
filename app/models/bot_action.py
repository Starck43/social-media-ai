from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

import sqlalchemy as sa
from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, relationship

from ..core.config import settings
from ..core.decorators import app_label
from ..types import BotActionStatus, BotActionType
from .base import Base, TenantScopedMixin, TimestampMixin


@app_label("social")
class BotAction(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "bot_actions"
    __table_args__ = (
        Index("ix_bot_actions_tenant_id", "tenant_id"),
        Index("ix_bot_actions_status", "status"),
        Index("ix_bot_actions_agent_scenario_id", "agent_scenario_id"),
        Index("ix_bot_actions_source_id", "source_id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)

    agent_scenario_id: Mapped[int] = Column(
        Integer, ForeignKey("social_manager.agent_scenarios.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[int] = Column(
        Integer, ForeignKey("social_manager.sources.id", ondelete="CASCADE"), nullable=False
    )
    analytics_id: Mapped[int | None] = Column(
        Integer, ForeignKey("social_manager.ai_analytics.id", ondelete="SET NULL"), nullable=True
    )

    action_type: Mapped[BotActionType] = BotActionType.sa_column(
        type_name="bot_action_type", nullable=False, store_as_name=True
    )
    status: Mapped[BotActionStatus] = BotActionStatus.sa_column(
        type_name="bot_action_status",
        nullable=False,
        default=BotActionStatus.PENDING,
        store_as_name=True,
        server_default="PENDING",
    )

    payload: Mapped[dict[str, Any]] = Column(JSON, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb"))
    result: Mapped[dict[str, Any] | None] = Column(JSON, nullable=True)
    error: Mapped[str | None] = Column(Text, nullable=True)

    dry_run: Mapped[bool] = Column(Boolean, nullable=False, default=True, server_default=sa.text("true"))
    confirmed_by: Mapped[int | None] = Column(
        Integer, ForeignKey("social_manager.tenant_users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = Column(DateTime(timezone=True), nullable=True)

    attempts: Mapped[int] = Column(Integer, nullable=False, default=0, server_default=sa.text("0"))

    scenario = relationship("AgentScenario", foreign_keys=[agent_scenario_id])
    source = relationship("Source", foreign_keys=[source_id])
    analytics = relationship("AIAnalytics", foreign_keys=[analytics_id])

    if TYPE_CHECKING:
        from .managers.base_manager import BaseManager
        from .managers.bot_action_manager import BotActionManager

        objects: ClassVar[BotActionManager | BaseManager]
    else:
        objects: ClassVar = None

    def __str__(self) -> str:
        return f"BotAction#{self.id} ({self.action_type.name}, {self.status.name})"


from .managers.bot_action_manager import BotActionManager  # noqa: E402

BotAction.objects = BotActionManager()
