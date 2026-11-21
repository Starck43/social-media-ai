"""Local Task identity and participation."""

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScopedMixin, TimestampMixin
from app.models.planner.task import S, TaskColumns, task_constraints


class Task(TaskColumns, Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "tasks"
    __table_args__ = (
        *task_constraints(__tablename__, "task"),
        CheckConstraint("visibility IN ('private','shared','workspace')", name="ck_task_visibility"),
        Index("ix_task_owner", "tenant_id", "owner_user_id", "id"),
        Index("ix_task_assignee", "tenant_id", "assignee_id", "id"),
        {"schema": S},
    )

    visibility: Mapped[str] = mapped_column(String(16), nullable=False)


class TaskParticipant(Base, TenantScopedMixin):
    __tablename__ = "task_participants"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "task_id"], [f"{S}.tasks.tenant_id", f"{S}.tasks.id"], ondelete="CASCADE"),
        CheckConstraint("role IN ('collaborator','watcher')", name="ck_task_participant_role"),
        {"schema": S},
    )

    tenant_id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(f"{S}.users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), primary_key=True)
