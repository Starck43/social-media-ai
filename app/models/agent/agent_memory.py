from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Column, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped

from ...core.config import settings
from ...core.decorators import app_label
from ..base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from ..managers.agent_memory_manager import AgentMemoryManager


@app_label("agent")
class AgentMemory(Base, TenantScopedMixin, TimestampMixin):
    """Small key/value scratchpad for the agent (the SOUL.md analogue).

    Deliberately not a vector store: the agent needs a handful of durable
    preferences ("digest tone", "quiet hours"), not semantic recall.
    """

    __tablename__ = "agent_memory"
    __table_args__ = (
        # Personal rows are isolated by user; NULL user_id is the owner-only
        # legacy workspace bucket and retains its own key namespace.
        Index(
            "uq_agent_memory_tenant_user_scope_key",
            "tenant_id",
            "user_id",
            "scope",
            "key",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "uq_agent_memory_tenant_scope_key_legacy",
            "tenant_id",
            "scope",
            "key",
            unique=True,
            postgresql_where=text("user_id IS NULL"),
        ),
        Index("ix_agent_memory_tenant_id", "tenant_id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    user_id: Mapped[int | None] = Column(
        Integer,
        ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="CASCADE"),
        nullable=True,
        comment="Personal memory owner; NULL is the legacy owner-only bucket",
    )
    scope: Mapped[str] = Column(String(20), default="global", nullable=False, server_default="global")
    key: Mapped[str] = Column(String(100), nullable=False)
    value: Mapped[str | None] = Column(Text, nullable=True)

    # Provenance: where the fact came from and how much we trust it.
    source: Mapped[str] = Column(
        String(20),
        nullable=False,
        default="manual",
        server_default="manual",
        comment="manual (memory_set) | learn (extracted from chat) | reflect (post-dedup)",
    )
    confidence: Mapped[float] = Column(Float, nullable=False, default=1.0, server_default="1.0")
    evidence_message_id: Mapped[int | None] = Column(
        Integer,
        ForeignKey(f"{settings.DB_SCHEMA}.agent_messages.id", ondelete="SET NULL"),
        nullable=True,
        comment="Chat message this fact was learned from (FK is SET NULL: messages can be cleared)",
    )

    if TYPE_CHECKING:
        from ..managers.base_manager import BaseManager

        objects: ClassVar[AgentMemoryManager | BaseManager]
    else:
        objects: ClassVar = None

    def __str__(self) -> str:
        return f"AgentMemory[{self.scope}]{self.key}"
