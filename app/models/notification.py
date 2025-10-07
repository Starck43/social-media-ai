from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Column, Integer, String, Text, Boolean, Enum
from sqlalchemy.orm import Mapped, mapped_column

from . import Base, TimestampMixin
from ..core.decorators import app_label
from ..types.models import NotificationType


@app_label("social")
class Notification(Base, TimestampMixin):
	__tablename__ = 'notifications'
	__table_args__ = {'schema': 'social_manager'}

	id: Mapped[int] = Column(Integer, primary_key=True)
	title: Mapped[str] = Column(String(200), nullable=False)
	message: Mapped[str] = Column(Text, nullable=False)
	notification_type: Mapped[NotificationType] = mapped_column(
		Enum(NotificationType, name='notification_type', schema='social_manager', inherit_schema=True)
	)  # 'alert', 'info', 'warning'
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
