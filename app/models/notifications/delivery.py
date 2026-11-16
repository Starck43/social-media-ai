"""Single-attempt external delivery owned by the notification surface."""

from datetime import datetime

from sqlalchemy import (
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


class NotificationDelivery(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["notification_id", "user_id"],
            [f"{S}.notification_receipts.notification_id", f"{S}.notification_receipts.user_id"],
            ondelete="CASCADE",
            name="fk_notification_delivery_receipt",
        ),
        UniqueConstraint("notification_id", "user_id", "channel", name="uq_notification_delivery"),
        CheckConstraint("channel IN ('telegram','vk','email','max')", name="ck_notification_delivery_channel"),
        CheckConstraint(
            "state IN ('pending','sending','sent','blocked','unknown')", name="ck_notification_delivery_state"
        ),
        Index("ix_notification_delivery_pending", "tenant_id", "state", "next_attempt_at"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    notification_id: Mapped[int] = mapped_column(ForeignKey(f"{S}.notifications.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey(f"{S}.users.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(60))
