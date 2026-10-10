"""Legacy AgentTask imports and unchanged manager-binding entry point."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, relationship

from ..core.config import settings
from ..types import AgentActionType, BotTriggerType
from ..core.decorators import app_label
from .base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from .agent_scenario import AgentScenario
    from .managers.agent_task_manager import AgentTaskManager
    from .source import Source
    from .tenant import Tenant


# Many-to-many between tasks and sources (replaces the old payload["source_ids"] list)

# isort: split
from .scheduling.agent_task import AgentTask, agent_task_sources

# isort: split
from .managers.agent_task_manager import AgentTaskManager  # noqa: E402

AgentTask.objects = AgentTaskManager()
