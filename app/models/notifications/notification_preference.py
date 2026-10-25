"""Per-user presentation preferences for the notification bell."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ...core.config import settings
from ..base import Base, TimestampMixin


class NotificationPreference(Base, TimestampMixin):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        CheckConstraint("\"start\" ~ '^(?:[01][0-9]|2[0-3]):[0-5][0-9]$'", name="ck_notification_preferences_start"),
        CheckConstraint("\"end\" ~ '^(?:[01][0-9]|2[0-3]):[0-5][0-9]$'", name="ck_notification_preferences_end"),
        CheckConstraint('NOT quiet OR "start" <> "end"', name="ck_notification_preferences_quiet_interval"),
        {"schema": settings.DB_SCHEMA},
    )

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    connections: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    tasks: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    popup: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    quiet: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    start: Mapped[str] = mapped_column(String(5), nullable=False, default="22:00", server_default="22:00")
    end: Mapped[str] = mapped_column(String(5), nullable=False, default="08:00", server_default="08:00")

    if TYPE_CHECKING:
        from ..managers.notification_preference_manager import NotificationPreferenceManager

        objects: ClassVar[NotificationPreferenceManager]
    else:
        objects: ClassVar = None
