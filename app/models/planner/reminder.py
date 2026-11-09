"""One user's durable reminder for one planner occurrence."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base, TenantScopedMixin, TimestampMixin

S = settings.DB_SCHEMA


class PlannerReminder(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "planner_reminders"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            [f"{S}.planner_tasks.tenant_id", f"{S}.planner_tasks.id"],
            ondelete="CASCADE",
            name="fk_planner_reminder_task",
        ),
        UniqueConstraint("tenant_id", "task_id", "recipient_user_id", "channel", name="uq_planner_reminder_owner"),
        CheckConstraint("channel IN ('inbox','telegram','vk','email','max')", name="ck_planner_reminder_channel"),
        CheckConstraint("preset IN ('day','week','month','custom')", name="ck_planner_reminder_preset"),
        CheckConstraint("state IN ('pending','deferred','delivered','cancelled')", name="ck_planner_reminder_state"),
        CheckConstraint("version > 0 AND generation > 0", name="ck_planner_reminder_generation"),
        CheckConstraint("(state = 'delivered') = (sent_at IS NOT NULL)", name="ck_planner_reminder_delivery_time"),
        Index("ix_planner_reminder_due", "tenant_id", "state", "next_attempt_at"),
        {"schema": S},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    recipient_user_id: Mapped[int] = mapped_column(ForeignKey(f"{S}.users.id", ondelete="CASCADE"), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="inbox", server_default="inbox")
    preset: Mapped[str] = mapped_column(String(20), nullable=False)
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    notification_id: Mapped[int | None] = mapped_column(ForeignKey(f"{S}.notifications.id", ondelete="SET NULL"))
    last_error: Mapped[str | None] = mapped_column(String(60))
    late: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    objects: ClassVar = None
