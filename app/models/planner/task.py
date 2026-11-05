"""Our task decisions only; no upstream object identity or mirrored calendar row."""

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base, TenantScopedMixin, TimestampMixin
from app.models.types.encrypted import EncryptedText

S = settings.DB_SCHEMA


class PlannerTask(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "planner_tasks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_planner_task_tenant"),
        UniqueConstraint("tenant_id", "series_id", "occurrence_key", name="uq_planner_occurrence"),
        UniqueConstraint("tenant_id", "previous_occurrence_id", name="uq_planner_successor"),
        ForeignKeyConstraint(
            ["tenant_id", "series_id"], [f"{S}.planner_tasks.tenant_id", f"{S}.planner_tasks.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "parent_id"], [f"{S}.planner_tasks.tenant_id", f"{S}.planner_tasks.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "previous_occurrence_id"],
            [f"{S}.planner_tasks.tenant_id", f"{S}.planner_tasks.id"],
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        # Global SET NULL preserves the task on contact erasure. The additional
        # deferred composite constraint prevents cross-workspace contact links
        # without requiring PostgreSQL 15's column-list SET NULL syntax.
        ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            [f"{S}.contacts.tenant_id", f"{S}.contacts.id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("priority IN ('low','medium','high','critical')", name="ck_planner_priority"),
        CheckConstraint("status IN ('todo','in_progress','done','cancelled')", name="ck_planner_status"),
        CheckConstraint("due_at IS NULL OR due_date IS NULL", name="ck_planner_deadline"),
        CheckConstraint(
            "recurrence IS NULL OR due_at IS NOT NULL OR due_date IS NOT NULL", name="ck_planner_recurrence_due"
        ),
        CheckConstraint("version > 0 AND occurrence_index >= 0", name="ck_planner_versions"),
        CheckConstraint("parent_id IS NULL OR parent_id != id", name="ck_planner_parent"),
        CheckConstraint("(status = 'done') = (completed_at IS NOT NULL)", name="ck_planner_completion"),
        Index("ix_planner_workspace_due", "tenant_id", "status", "due_at", "due_date"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey(f"{S}.users.id", ondelete="CASCADE"), nullable=False)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey(f"{S}.users.id", ondelete="SET NULL"))
    title_encrypted: Mapped[str] = mapped_column(EncryptedText(), nullable=False)
    description_encrypted: Mapped[str] = mapped_column(EncryptedText(), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_date: Mapped[date | None] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    priority: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="todo")
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey(f"{S}.contacts.id", ondelete="SET NULL"))
    parent_id: Mapped[int | None] = mapped_column(Integer)
    series_id: Mapped[int | None] = mapped_column(Integer)
    previous_occurrence_id: Mapped[int | None] = mapped_column(Integer)
    occurrence_key: Mapped[str] = mapped_column(String(64), nullable=False)
    occurrence_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recurrence: Mapped[str | None] = mapped_column(Text)
    recurrence_source: Mapped[str] = mapped_column(
        String(100), nullable=False, default="planner", server_default="planner"
    )
    # One encrypted series template, not expanded future occurrence rows. An
    # occurrence-only edit must not silently change the next task's defaults.
    series_template_encrypted: Mapped[str] = mapped_column(EncryptedText(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
