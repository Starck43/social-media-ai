from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Column, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped

from ..core.config import settings
from ..core.decorators import app_label
from .base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from .managers.agent_memory_manager import AgentMemoryManager


@app_label("social")
class AgentMemory(Base, TenantScopedMixin, TimestampMixin):
    """Small key/value scratchpad for the agent (the SOUL.md analogue).

    Deliberately not a vector store: the agent needs a handful of durable
    preferences ("digest tone", "quiet hours"), not semantic recall.
    """

    __tablename__ = "agent_memory"
    __table_args__ = (
        # Scoped by tenant: two workspaces may both store "digest_tone", and
        # `scope` only groups facts inside one workspace.
        UniqueConstraint("tenant_id", "scope", "key", name="uq_agent_memory_tenant_scope_key"),
        Index("ix_agent_memory_tenant_id", "tenant_id"),
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
