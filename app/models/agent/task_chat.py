"""One private task conversation per (workspace, user, canonical task).

The binding is the only new persistence this feature adds: the transcript,
pending confirmation and message actions stay in `agent_sessions` /
`agent_messages`. A session belongs to at most one binding, and the actor is
part of the unique key, so two members with legitimate access to the same task
never share a transcript.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, TenantScopedMixin, TimestampMixin
from ..planner.task import S

if TYPE_CHECKING:
    from ..managers.task_chat_manager import TaskChatBindingManager


class TaskChatBinding(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "task_chat_bindings"
    __table_args__ = (
        # Composite target keys keep the binding tenant-checked in the database,
        # not only in the service that creates it.
        ForeignKeyConstraint(["tenant_id", "task_id"], [f"{S}.tasks.tenant_id", f"{S}.tasks.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            [f"{S}.agent_sessions.tenant_id", f"{S}.agent_sessions.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "user_id", "task_id", name="uq_task_chat_binding_actor"),
        UniqueConstraint("tenant_id", "session_id", name="uq_task_chat_binding_session"),
        Index("ix_task_chat_binding_user", "tenant_id", "user_id"),
        {"schema": S},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(f"{S}.users.id", ondelete="CASCADE"), nullable=False)
    task_id: Mapped[int] = mapped_column(nullable=False)
    session_id: Mapped[int] = mapped_column(nullable=False)

    if TYPE_CHECKING:
        objects: ClassVar[TaskChatBindingManager]
    else:
        objects: ClassVar = None

    def __str__(self) -> str:
        return f"TaskChatBinding#{self.id}[task={self.task_id}, user={self.user_id}]"
