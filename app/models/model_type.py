from typing import TYPE_CHECKING

from sqlalchemy import Integer, String, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin
from ..core.config import settings

if TYPE_CHECKING:
	from . import Permission


class ModelType(Base, TimestampMixin):
	__tablename__ = "model_types"
	__table_args__ = {'schema': settings.DB_SCHEMA}

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	app_name: Mapped[str] = mapped_column(String(100), nullable=False)
	model_name: Mapped[str] = mapped_column(String(100), nullable=False)
	table_name: Mapped[str] = mapped_column(String(100), nullable=False)
	description: Mapped[str] = mapped_column(Text, nullable=True)
	is_managed: Mapped[bool] = mapped_column(Boolean, default=True)

	# Relationship one-to-many between ContentType and Permission
	permissions: Mapped[list["Permission"]] = relationship(
		"Permission",
		back_populates="model_type",
		cascade="all, delete-orphan",  # ✅ Удаляем permissions при удалении model_type
		passive_deletes=True
	)

	def __str__(self) -> str:
		return f"{self.app_name}.{self.model_name}"
