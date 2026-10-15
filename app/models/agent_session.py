"""Legacy imports; canonical model and manager registration live in the domain facade."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import JSON, Boolean, Column, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped

from ..core.config import settings
from ..core.decorators import app_label
from .base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from .managers.agent_session_manager import AgentSessionManager

# isort: split
from .agent.agent_session import AgentSession

# isort: split
from .managers.agent_session_manager import AgentSessionManager  # noqa: E402
