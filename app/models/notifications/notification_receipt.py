"""A global user's inbox entry; workspace is origin metadata on the event."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from sqlalchemy import DateTime, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...core.config import settings
from ..base import Base, TimestampMixin


class NotificationReceipt(Base, TimestampMixin):
    __tablename__ = "notification_receipts"
    __table_args__ = (
        UniqueConstraint("notification_id", "user_id", name="uq_notification_receipt_owner"),
        Index("ix_notification_receipts_user_id", "user_id"),
        {"schema": settings.DB_SCHEMA},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    notification_id: Mapped[int] = mapped_column(
        ForeignKey(f"{settings.DB_SCHEMA}.notifications.id", ondelete="CASCADE")
    )
    user_id: Mapped[int] = mapped_column(ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="CASCADE"))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    objects: ClassVar = None
