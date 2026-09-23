from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Column, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped

from ..core.config import settings
from ..core.decorators import app_label
from . import Base, TimestampMixin

if TYPE_CHECKING:
    from .managers.agent_memory_manager import AgentMemoryManager


@app_label("social")
class AgentMemory(Base, TimestampMixin):
    """Small key/value scratchpad for the agent (the SOUL.md analogue).

    Deliberately not a vector store: the agent needs a handful of durable
    preferences ("digest tone", "quiet hours"), not semantic recall.
    """

    __tablename__ = "agent_memory"
    __table_args__ = (
        UniqueConstraint("scope", "key", name="uq_agent_memory_scope_key"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    scope: Mapped[str] = Column(String(20), default="global", nullable=False, server_default="global")
    key: Mapped[str] = Column(String(100), nullable=False)
    value: Mapped[str | None] = Column(Text, nullable=True)

    if TYPE_CHECKING:
        from .managers.base_manager import BaseManager

        objects: ClassVar[AgentMemoryManager | BaseManager]
    else:
        objects: ClassVar = None

    def __str__(self) -> str:
        return f"AgentMemory[{self.scope}]{self.key}"


from .managers.agent_memory_manager import AgentMemoryManager  # noqa: E402

AgentMemory.objects = AgentMemoryManager()
