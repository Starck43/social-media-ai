from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Any

from sqlalchemy import Column, Integer, String, DateTime, Boolean, JSON, text
from sqlalchemy.orm import Mapped, relationship, mapped_column, validates

from .base import Base
from ..core.config import settings
from ..core.decorators import app_label
from ..types import PlatformType

if TYPE_CHECKING:
	from . import Source


@app_label("social")
class Platform(Base):
	__tablename__ = 'platforms'
	__table_args__ = {'schema': settings.DB_SCHEMA}

	id: Mapped[int] = Column(Integer, primary_key=True)
	name: Mapped[str] = Column(String(50), unique=True, nullable=False)  # 'vk', 'telegram'
	platform_type: Mapped[PlatformType] = PlatformType.sa_column(
		type_name='platform_type',
		nullable=False,
		store_as_name=False  # Хранить как значения ('vk', 'telegram')
	)
	base_url: Mapped[str] = mapped_column(String(255), nullable=False)
	params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
	# {
	#     "api_base_url": "https://api.vk.com/method",
	#     "api_version": "5.199",
	#     "auth_type": "oauth",
	# }
	is_active: Mapped[bool] = Column(Boolean, default=True, server_default=text('true'))
	rate_limit_remaining: Mapped[int] = Column(Integer, nullable=True)
	rate_limit_reset_at: Mapped[DateTime] = Column(DateTime, nullable=True)

	# Relationships
	sources: Mapped[list["Source"]] = relationship(
		"Source",
		back_populates="platform",
		cascade="all, delete-orphan",  # ✅ Удаляем источники при удалении платформы
		passive_deletes=True
	)

	# Manager will be set after class definition
	if TYPE_CHECKING:
		from .managers.base_manager import BaseManager
		from .managers.platform_manager import PlatformManager
		objects: ClassVar[PlatformManager | BaseManager]
	else:
		objects: ClassVar = None

	def __str__(self) -> str:
		return f"{self.name}"

	@validates('base_url', 'api_url')
	def normalize_base_url(self, key, url: str) -> str:
		return url.rstrip('/')


from .managers.platform_manager import PlatformManager  # noqa: E402

Platform.objects = PlatformManager()
