from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Column, Integer, String, Text, ForeignKey, Boolean
from sqlalchemy.orm import relationship, Mapped

from . import Base
from ..core.decorators import app_label

if TYPE_CHECKING:
	from . import User


@app_label("social")
class Notification(Base):
	__tablename__ = 'notifications'
	__table_args__ = {'schema': 'social_manager'}

	id: Mapped[int] = Column(Integer, primary_key=True)
	user_id: Mapped[int] = Column(Integer, ForeignKey('social_manager.users.id'))
	title: Mapped[str] = Column(String(200), nullable=False)
	message: Mapped[str] = Column(Text, nullable=False)
	notification_type: Mapped[str] = Column(String(50))  # 'alert', 'info', 'warning'
	is_read: Mapped[bool] = Column(Boolean, default=False)
	related_entity_type: Mapped[str] = Column(String(50))  # 'post', 'comment', 'group'
	related_entity_id: Mapped[int] = Column(Integer)

	# Relationship one-to-many to User
	user: Mapped['User'] = relationship("User", back_populates="notifications")

	# Manager will be set after class definition to avoid circular imports
	if TYPE_CHECKING:
		from .managers.base_manager import BaseManager
		from .managers.notification_manager import NotificationManager
		objects: ClassVar[NotificationManager | BaseManager]
	else:
		objects: ClassVar = None


from .managers.notification_manager import NotificationManager  # noqa: E402
Notification.objects = NotificationManager()
