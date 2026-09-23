from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped

from ..core.config import settings
from ..core.decorators import app_label
from . import Base, TimestampMixin

if TYPE_CHECKING:
    from .managers.agent_session_manager import AgentSessionManager


@app_label("social")
class AgentSession(Base, TimestampMixin):
    """One conversation with the agent (a private chat or a channel feed).

    `state` holds volatile per-chat bookkeeping — most importantly the Telegram
    `update_offset`, so a restart does not replay old messages.
    """

    __tablename__ = "agent_sessions"
    __table_args__ = (
        UniqueConstraint("channel", "chat_id", name="uq_agent_session_chat"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    channel: Mapped[str] = Column(String(20), nullable=False)  # 'telegram' | 'max'
    chat_id: Mapped[str] = Column(String(100), nullable=False)
    kind: Mapped[str] = Column(String(20), default="private", nullable=False, server_default="private")
    is_owner: Mapped[bool] = Column(Boolean, default=False, nullable=False, server_default="false")
    is_active: Mapped[bool] = Column(Boolean, default=True, nullable=False, server_default="true")
    state: Mapped[dict[str, Any] | None] = Column(
        # JSON, not JSONB: cheaper writes, and we never query inside it.
        JSON,
        nullable=True,
    )
    last_message_at: Mapped[datetime | None] = Column(DateTime(timezone=True), nullable=True)

    if TYPE_CHECKING:
        from .managers.base_manager import BaseManager

        objects: ClassVar[AgentSessionManager | BaseManager]
    else:
        objects: ClassVar = None

    def __str__(self) -> str:
        return f"AgentSession#{self.id}[{self.channel}:{self.chat_id}]"

    @property
    def update_offset(self) -> int | None:
        """Telegram long-polling offset stored in `state`."""
        return (self.state or {}).get("update_offset")

    @property
    def pending_action(self) -> dict[str, Any] | None:
        """Write-tool call awaiting the owner's confirmation."""
        return (self.state or {}).get("pending_action")

    async def save_state(self, state: dict[str, Any]) -> None:
        """Persist the volatile per-chat state dict."""
        from .managers.agent_session_manager import agent_sessions

        await agent_sessions.update_by_id(self.id, state=state)
        self.state = state

    async def append(
        self,
        role: str,
        content: str | None = None,
        *,
        tool_calls: list[dict[str, Any]] | None = None,
        tool_call_id: str | None = None,
    ) -> Any:
        """Persist one conversation turn and bump the activity timestamp."""
        from .managers.agent_message_manager import agent_messages
        from .managers.agent_session_manager import agent_sessions

        row = await agent_messages.append(
            session_id=self.id,
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_name=tool_call_id,
        )
        await agent_sessions.touch(self.id)
        return row

    async def touch(self) -> None:
        """Record activity (used for idle detection / retention)."""
        from .managers.agent_session_manager import agent_sessions

        await agent_sessions.touch(self.id)

    async def history(self) -> list[dict[str, Any]]:
        """Recent turns of the conversation as OpenAI-style messages."""
        from app.agent.session import load_history

        return await load_history(self)


from .managers.agent_session_manager import AgentSessionManager  # noqa: E402

AgentSession.objects = AgentSessionManager()
