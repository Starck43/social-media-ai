from datetime import datetime
from typing import ClassVar, TypeVar, TYPE_CHECKING

from sqlalchemy import func, DateTime, MetaData, Enum
from sqlalchemy.orm import mapped_column, Mapped, declared_attr, DeclarativeBase, Session

T = TypeVar('T', bound='Base')


class TimestampMixin:
	@declared_attr
	def created_at(self) -> Mapped[datetime]:
		return mapped_column(
			DateTime(timezone=True),
			nullable=False,
			server_default=func.now(),
		)

	@declared_attr
	def updated_at(self) -> Mapped[datetime]:
		return mapped_column(
			DateTime(timezone=True),
			nullable=False,
			server_default=func.now(),
			onupdate=func.now(),
		)


class Base(DeclarativeBase):
	"""Base model class with common functionality."""
	__allow_unmapped__ = True

	metadata = MetaData(schema="social_manager")

	# Manager will be set after class definition to avoid circular imports
	if TYPE_CHECKING:
		from app.models.managers.base_manager import BaseManager
		objects: ClassVar[BaseManager]
	else:
		objects = None

	def save(self: T, db: Session = None, **kwargs) -> T:
		"""
		Update model attributes and save to database.
		This is a custom save method to avoid conflicts with SQLAlchemy's internals.

		Args:
			db: Optional SQLAlchemy session. If not provided, will create a new session.
			**kwargs: Attributes to update

		Returns:
			The updated model instance
		"""
		for key, value in kwargs.items():
			if hasattr(self, key) and not key.startswith('_'):
				setattr(self, key, value)

		if db is None:
			from app.core.database import SessionLocal
			session = SessionLocal()
			try:
				session.add(self)
				session.commit()
				session.refresh(self)
			finally:
				session.close()
		else:
			db.add(self)
			db.commit()
			db.refresh(self)
		return self

	def delete(self: T, db: Session = None) -> bool:
		"""
		Delete the model instance from the database.

		Args:
			db: Optional SQLAlchemy session. If not provided, will create a new session.

		Returns:
			bool: True if deletion successful
		"""
		if db is None:
			from app.core.database import SessionLocal
			session = SessionLocal()
			try:
				session.delete(self)
				session.commit()
				return True
			finally:
				session.close()
		else:
			db.delete(self)
			db.commit()
			return True
