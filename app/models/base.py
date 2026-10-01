from datetime import datetime
from typing import ClassVar, TYPE_CHECKING

from sqlalchemy import func, DateTime, MetaData, ForeignKey
from sqlalchemy.orm import mapped_column, Mapped, declared_attr, DeclarativeBase

from app.core.config import settings


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
	- `create()` stamps `tenant_id` from the context and refuses to run without a tenant (fail-closed);
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

	# Default for any table that does not restate it in `__table_args__`; the
	# schema is a setting, so the test suite can run against its own.
	metadata = MetaData(schema=settings.DB_SCHEMA)

	# Manager will be set after class definition to avoid circular imports
	if TYPE_CHECKING:
		from app.models.managers.base_manager import BaseManager

		objects: ClassVar[BaseManager]
	else:
		objects = None
