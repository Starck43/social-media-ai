"""Legacy AgentMemory imports and unchanged manager-binding entry point."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Column, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped

from ..core.config import settings
from ..core.decorators import app_label
from .base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from .managers.agent_memory_manager import AgentMemoryManager

# isort: split
from .agent.agent_memory import AgentMemory

# isort: split
from .managers.agent_memory_manager import AgentMemoryManager  # noqa: E402

AgentMemory.objects = AgentMemoryManager()
