"""Legacy AgentMessage imports and unchanged manager-binding entry point."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import Column, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped

from ..core.config import settings
from ..core.decorators import app_label
from .base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from .managers.agent_message_manager import AgentMessageManager

# isort: split
from .agent.agent_message import AgentMessage

# isort: split
from .managers.agent_message_manager import AgentMessageManager  # noqa: E402

AgentMessage.objects = AgentMessageManager()
