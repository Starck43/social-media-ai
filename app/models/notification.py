from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Column, Integer, String, Text, Boolean, Enum
from sqlalchemy.orm import Mapped, mapped_column

from . import Base, TimestampMixin
from ..core.config import settings
from ..core.decorators import app_label
from ..types.models import NotificationType


@app_label("social")
class Notification(Base, TimestampMixin):
	__tablename__ = 'notifications'
	__table_args__ = {'schema': settings.DB_SCHEMA}

	id: Mapped[int] = Column(Integer, primary_key=True)
	title: Mapped[str] = Column(String(200), nullable=False)
	message: Mapped[str] = Column(Text, nullable=False)
	# Используем существующий тип notification_type из базы данных
	notification_type: Mapped[NotificationType] = NotificationType.sa_column(
		type_name='notification_type',
		store_as_name=True  # Хранить как имена (REPORT_READY, MOOD_CHANGE, etc.)
	)
	is_read: Mapped[bool] = Column(Boolean, default=False)
	related_entity_type: Mapped[str] = Column(String(50))  # 'source', 'platform', 'analysis'
	related_entity_id: Mapped[int] = Column(Integer)

	# Manager will be set after class definition to avoid circular imports
	if TYPE_CHECKING:
		from .managers.base_manager import BaseManager
		from .managers.notification_manager import NotificationManager
		objects: ClassVar[NotificationManager | BaseManager]
	else:
		objects: ClassVar = None


from .managers.notification_manager import NotificationManager  # noqa: E402

Notification.objects = NotificationManager()
