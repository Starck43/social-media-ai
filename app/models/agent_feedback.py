"""Legacy AgentFeedback imports and unchanged manager-binding entry point."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Column, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped

from ..core.config import settings
from ..core.decorators import app_label
from .base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from .managers.agent_feedback_manager import AgentFeedbackManager

# isort: split
from .agent.agent_feedback import AgentFeedback

# isort: split
from .managers.agent_feedback_manager import AgentFeedbackManager  # noqa: E402

AgentFeedback.objects = AgentFeedbackManager()
