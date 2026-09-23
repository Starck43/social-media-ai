from datetime import datetime
from typing import ClassVar, TypeVar, TYPE_CHECKING

from sqlalchemy import func, DateTime, MetaData, ForeignKey
from sqlalchemy.orm import mapped_column, Mapped, declared_attr, DeclarativeBase, Session

T = TypeVar("T", bound="Base")


class TimestampMixin:
	@declared_attr
	def created_at(self) -> Mapped[datetime]:
		return mapped_column(
			DateTime(timezone=True),  # type: ignore[arg-type]
			nullable=False,
			server_default=func.now(),
		)

	@declared_attr
	def updated_at(self) -> Mapped[datetime]:
		return mapped_column(
			DateTime(timezone=True),  # type: ignore[arg-type]
			nullable=False,
			server_default=func.now(),
			onupdate=func.now(),
		)


class TenantScopedMixin:
	"""Marks a model as tenant-owned: every row carries `tenant_id`.

	Enforcement lives in `BaseManager`/`QuerySet`, not here:
	- SELECT queries are filtered to `current_tenant_id()` (unless superuser bypass);
	- `create()` stamps `tenant_id` from the context and refuses to run
	  without a tenant (fail-closed);
	- `update_by_id`/`delete_by_id` re-fetch the row through the scoped
	  queryset, so a cross-tenant id silently becomes "not found".

	Models that must stay global (`Platform`, `LLMProvider`, `LLMModel`,
	`ModelType`, `Permission`, `Role`, and the `Tenant*` tables themselves,
	which are resolved before a tenant context exists) do NOT use this mixin.
	"""

	__tenant_scoped__: ClassVar[bool] = True

	@declared_attr
	def tenant_id(cls) -> Mapped[int]:
		from ..core.config import settings

		return mapped_column(
			ForeignKey(f"{settings.DB_SCHEMA}.tenants.id", ondelete="CASCADE"),
			nullable=False,
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

	def save(self, db: Session | None = None, **kwargs) -> T:
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
			if hasattr(self, key) and not key.startswith("_"):
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

	def delete(self, db: Session | None = None) -> bool:
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
