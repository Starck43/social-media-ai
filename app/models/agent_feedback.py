from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Column, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped

from ..core.config import settings
from ..core.decorators import app_label
from .base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from .managers.agent_feedback_manager import AgentFeedbackManager


@app_label("social")
class AgentFeedback(Base, TenantScopedMixin, TimestampMixin):
    """Owner's rating of an assistant reply (/good, /bad <note>).

    Feedback is the training signal for prompt evolution: `reflect` aggregates
    negative notes, and a low-rated task run is what justifies a confirmed
    prompt edit. It never mutates prompts by itself.
    """

    __tablename__ = "agent_feedback"
    __table_args__ = (
        Index("ix_agent_feedback_tenant_id", "tenant_id"),
        Index("ix_agent_feedback_session_id", "session_id"),
        Index("ix_agent_feedback_vote", "vote"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    session_id: Mapped[int] = Column(
        Integer,
        ForeignKey(f"{settings.DB_SCHEMA}.agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The rated assistant message; SET NULL because /stop clears history.
    message_id: Mapped[int | None] = Column(
        Integer,
        ForeignKey(f"{settings.DB_SCHEMA}.agent_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    vote: Mapped[str] = Column(String(10), nullable=False, comment="good | bad")
    note: Mapped[str | None] = Column(Text, nullable=True, comment="Free-text complaint attached to /bad")
    voter_channel: Mapped[str | None] = Column(String(20), nullable=True)
    voter_external_id: Mapped[str | None] = Column(String(100), nullable=True, comment="Channel user id of the voter")

    if TYPE_CHECKING:
        from .managers.base_manager import BaseManager

        objects: ClassVar[AgentFeedbackManager | BaseManager]
    else:
        objects: ClassVar = None

    def __str__(self) -> str:
        return f"AgentFeedback#{self.id}[{self.vote}]"


from .managers.agent_feedback_manager import AgentFeedbackManager  # noqa: E402

AgentFeedback.objects = AgentFeedbackManager()
