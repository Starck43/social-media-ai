from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import Column, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped

from ..core.config import settings
from ..core.decorators import app_label
from .base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from .managers.agent_message_manager import AgentMessageManager


@app_label("social")
class AgentMessage(Base, TenantScopedMixin, TimestampMixin):
    """One turn in an agent conversation: user input, assistant reply, tool result.

    Tokens are counted separately per call so cost reporting can tell how much
    of a conversation was spent on planning versus tool round-trips.
    """

    __tablename__ = "agent_messages"
    __table_args__ = (
        # Explicit name: the column-level index=True autogenerates a
        # schema-prefixed name, which would diverge from the migration.
        Index("ix_agent_messages_session_id", "session_id"),
        Index("ix_agent_messages_tenant_id", "tenant_id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    session_id: Mapped[int] = Column(
        Integer,
        ForeignKey(f"{settings.DB_SCHEMA}.agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 'user' | 'assistant' | 'tool'
    role: Mapped[str] = Column(String(20), nullable=False)
    content: Mapped[str | None] = Column(Text, nullable=True)
    tool_calls: Mapped[list[dict[str, Any]] | None] = Column(JSON, nullable=True)
    tool_name: Mapped[str | None] = Column(String(64), nullable=True)
    tokens: Mapped[int | None] = Column(Integer, nullable=True)
    cost: Mapped[float | None] = Column(Float, nullable=True)  # USD

    if TYPE_CHECKING:
        from .managers.base_manager import BaseManager

        objects: ClassVar[AgentMessageManager | BaseManager]
    else:
        objects: ClassVar = None

    def __str__(self) -> str:
        return f"AgentMessage#{self.id}[{self.role}]"


from .managers.agent_message_manager import AgentMessageManager  # noqa: E402

AgentMessage.objects = AgentMessageManager()
