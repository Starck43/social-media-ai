"""Workspace notification feed row (shared read state, generic entity attribution)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Boolean, Column, Index, Integer, String, Text
from sqlalchemy.orm import Mapped

from ...core.config import settings
from ...core.decorators import app_label
from ...types import NotificationType
from ..base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from ..managers.notification_manager import NotificationManager


@app_label("notifications")
class Notification(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_tenant_id", "tenant_id"), {"schema": settings.DB_SCHEMA})

    id: Mapped[int] = Column(Integer, primary_key=True)
    title: Mapped[str] = Column(String(200), nullable=False)
    message: Mapped[str] = Column(Text, nullable=False)
    # Native PostgreSQL enum `notification_type`, stored as member names (REPORT_READY, ...).
    notification_type: Mapped[NotificationType] = NotificationType.sa_column(
        type_name="notification_type", store_as_name=True
    )
    is_read: Mapped[bool] = Column(Boolean, default=False)
    # Generic attribution ('source', 'task', 'platform', ...), not a foreign key.
    related_entity_type: Mapped[str] = Column(String(50))
    related_entity_id: Mapped[int] = Column(Integer)

    # Bound once by app.models.bootstrap after every mapper is registered.
    if TYPE_CHECKING:
        objects: ClassVar[NotificationManager]
    else:
        objects: ClassVar = None
