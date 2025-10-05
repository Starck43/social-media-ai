"""
Query Manager for SQLAlchemy models.
This module provides a BaseManager class can be used to create custom query methods for SQLAlchemy models.
"""
from datetime import datetime
from typing import Any, Generic, TypeVar, Type, Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import ColumnElement
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload, QueryableAttribute, InstrumentedAttribute
from sqlalchemy.sql import and_, exists, select, Select

from app.core.database import with_db_session
from app.schemas.common import PaginationResult

M = TypeVar('M')


class BaseManager(Generic[M]):
	"""
	An async manager that provides query methods for SQLAlchemy models.

	Usage:
		class UserManager(BaseManager[User]):
			# Option 1: With an explicit session (original way)
			async def active(self, session: AsyncSession) → list[User]:
				return await self.filter(session, User.is_active == True)

			# Option 2: Without a session (Django-style)
			async def with_email_domain(self, domain: str) → list[User]:
				return await self.filter(User.email.like(f'%@{domain}'))

		class User(Base):
			__tablename__ = 'users'
			objects = UserManager(User)

		# Usage examples:
		# users = await User.objects.filter(is_active=True)
		# user = await User.objects.get(id=1)
	"""

	def __init__(self, model: Type[M] | None = None) -> None:
		self._prefetch_loads: list[str] = []
		self._eager_loads: list[str] = []
		self._annotations: dict[str, Any] = {}
		self.model = model

	def __get__(self, instance: Any, objtype: Type[Any] = None) -> 'BaseManager[M]':
		"""Support for using the manager as a class attribute."""
		if instance is not None:
			raise AttributeError("Manager isn't accessible via %s instances" % objtype.__name__)

		if self.model is None and objtype is not None:
			self.model = objtype

		return self

	@staticmethod
	def _get_session(**kwargs: dict) -> AsyncSession:
		session = kwargs.pop('session', None)
		if session is None:
			raise ValueError(
				"Session is required. Either pass it as 'session' parameter or use Depends(get_db)")
		if not isinstance(session, AsyncSession):
			raise TypeError(f"Expected AsyncSession, got {type(session).__name__}")
		return session

	def _build_filter_conditions(self, filters: dict[str, Any]) -> list[Any]:
		"""
		Build filter conditions from a dictionary of filters.

		Args:
			filters: Dictionary of field names and values to filter by

		Returns:
			List of SQLAlchemy filter conditions

		Example:
			conditions = self._build_filter_conditions({
				'is_active': True,
				'role': ['admin', 'moderator']
			})
			stmt = stmt.where(*conditions)
		"""
		filter_conditions = []
		for key, value in filters.items():
			if hasattr(self.model, key):
				column = getattr(self.model, key)
				if isinstance(value, (list, tuple)):
					filter_conditions.append(column.in_(value))
				else:
					filter_conditions.append(column == value)
		return filter_conditions

	async def get_queryset(self, **filters: Any) -> Select[tuple[M]]:
		"""
		Get the base query for this manager with applied options.

		This method is the foundation for all queries and applies:
		— Eager loading (joined load)
		— Prefetch loading (selectin load)
		— Annotations
		— Custom filters

		Args:
			**filters: Field filters to apply (e.g., is_active=True, role__in=['admin', 'moderator'])

		Returns:
			Select[tuple[M]]: SQL select query

		Example:
			# Basic usage
			stmt = await User.objects.get_queryset()

			# With filters
			stmt = await User.objects.get_queryset(is_active=True)

			# With list filters
			stmt = await User.objects.get_queryset(role__in=['admin', 'moderator'])
		"""
		stmt = select(self.model)

		# Apply filters if any
		if filters:
			filter_conditions = self._build_filter_conditions(filters)
			if filter_conditions:
				stmt = stmt.where(*filter_conditions)

		# Apply eager loading
		for relation in getattr(self, '_eager_loads', []):
			stmt = stmt.options(joinedload(getattr(self.model, relation)))

		# Apply prefetch loading
		for relation in getattr(self, '_prefetch_loads', []):
			stmt = stmt.options(selectinload(getattr(self.model, relation)))

		# Apply annotations
		for alias, annotation in getattr(self, '_annotations', {}).items():
			stmt = stmt.add_columns(annotation.label(alias))

		return stmt

	def _apply_eager_loads(self, stmt: Select[tuple[M]]) -> Select[tuple[M]]:
		"""Apply eager loading options to the query."""
		if not self._eager_loads:
			return stmt

		options = []
		for rel in self._eager_loads:
			if isinstance(rel, str):
				rel_attr = getattr(self.model, rel, None)
				if rel_attr is not None and hasattr(rel_attr, 'property') and rel_attr.property.is_attribute:
					options.append(joinedload(rel_attr))
			elif isinstance(rel, (QueryableAttribute, InstrumentedAttribute)):
				options.append(joinedload(rel))

		return stmt.options(*options) if options else stmt

	def _apply_prefetch_loads(self, stmt: Select[tuple[M]]) -> Select[tuple[M]]:
		"""Apply prefetch loading options to the query."""
		if not hasattr(self, '_prefetch_loads') or not self._prefetch_loads:
			return stmt

		options = []
		for rel in self._prefetch_loads:
			if isinstance(rel, str):
				rel_attr = getattr(self.model, rel, None)
				if rel_attr is not None and hasattr(rel_attr, 'property') and rel_attr.property.is_attribute:
					options.append(selectinload(rel_attr))
			elif isinstance(rel, (QueryableAttribute, InstrumentedAttribute)):
				options.append(selectinload(rel))

		return stmt.options(*options) if options else stmt

	@with_db_session
	async def all(self, **kwargs: Any) -> Sequence[M]:
		"""
		Return all instances of the model.

		Example:
			users = await User.objects.all()
		"""
		session = kwargs['session']
		stmt = await self.get_queryset()
		result = await session.execute(stmt)
		return result.scalars().all()

	@with_db_session
	async def get(self, **kwargs: Any) -> Optional[M]:
		"""
		Return the object matching the given filters or None if not found.

		Args:
			**kwargs: Filter conditions

		Example:
			user = await User.objects.get(id=1)
			user = await User.objects.get(email="user@example.com")
		"""
		"""Получить объект по условиям или None, если не найден."""
		session = self._get_session(**kwargs)
		stmt = await self.get_queryset(**kwargs)
		result = await session.execute(stmt)
		return result.scalars().first()

	@with_db_session
	async def get_or_404(self, **kwargs: Any) -> M:
		"""
		Return the object matching the given filters or raise 404 if not found.

		Example:
			user = await User.objects.get_or_404(id=1)
		"""
		instance = await self.get(**kwargs)
		if instance is None:
			raise HTTPException(
				status_code=404,
				detail=f"{self.model.__name__} not found"
			)
		return instance

	@with_db_session
	async def exclude(self, **kwargs: Any) -> Sequence[M]:
		"""
		Exclude objects matching the given filters.

		Example:
			# Get all users except admins
			non_admins = await User.objects.exclude(role='admin')
		"""
		session = self._get_session(**kwargs)
		stmt = await self.get_queryset()

		for k, v in kwargs.items():
			column: ColumnElement = getattr(self.model, k)
			stmt = stmt.filter(column != v)

		result = await session.execute(stmt)
		return result.scalars().all()

	@with_db_session
	async def exists(self, **kwargs: Any) -> bool:
		"""
		Check if any object exists matching the given filters.

		Args:
			**kwargs: Filter conditions.
		"""
		session = self._get_session(**kwargs)

		# Use get_queryset to apply all standard filters and options
		stmt = await self.get_queryset(**kwargs)
		# Optimize by limiting to 1 since we only need to know if any exist
		stmt = stmt.limit(1)

		result = await session.execute(stmt)
		return result.scalars().first() is not None
	
	@with_db_session
	async def order_by(self, *clauses: Any, **kwargs: Any) -> Sequence[M]:
		"""
		Order results by the given clauses.

		Example:
			# Get users ordered by creation date (newest first)
			users = await User.objects.order_by(User.created_at.desc())
		"""
		session = self._get_session(**kwargs)

		stmt = await self.get_queryset(**kwargs)
		stmt = stmt.order_by(*clauses)

		result = await session.execute(stmt)
		return result.scalars().all()

	@with_db_session
	async def filter(self, *criterion: ColumnElement[bool], **kwargs: ColumnElement[dict[str, Any]]) -> Sequence[M]:
		"""
		Return objects matching the given filters.

		Can be used with both SQLAlchemy expressions and keyword arguments:

		# Using SQLAlchemy expressions
		users = await User.objects.filter(
			User.email.endswith('@example.com'),
			User.is_active == True
		)

		# Using keyword arguments (backward compatible)
		users = await User.objects.filter(
			email__endswith='@example.com',
			is_active=True
		)

		# Or combine both styles
		from sqlalchemy import or_
		users = await User.objects.filter(
			or_(
				User.email.endswith('@example.com'),
				User.username.like('admin%')
			),
			is_active=True
		)
		"""
		session = self._get_session(**kwargs)

		# Start with base query
		stmt = await self.get_queryset()

		# Process SQLAlchemy expressions if any
		if criterion:
			stmt = stmt.where(*criterion)

		# Process keyword arguments (backward compatible)
		if kwargs:
			conditions: list[ColumnElement[bool]] = []

			# Handle special lookups like field__startswith, field__contains, etc.
			for key, value in kwargs.items():
				if '__' in key:
					field_name, lookup = key.split('__', 1)
					field = getattr(self.model, field_name, None)
					if field is None:
						raise AttributeError(f"Model {self.model.__name__} has no attribute {field_name}")

					if lookup == 'in' and isinstance(value, (list, tuple)):
						conditions.append(field.in_(value))
					elif lookup == 'isnull':
						if value:
							conditions.append(field.is_(None))
						else:
							conditions.append(field.is_not(None))
					elif lookup == 'contains':
						conditions.append(field.contains(str(value)))
					elif lookup == 'startswith':
						conditions.append(field.startswith(str(value)))
					elif lookup == 'endswith':
						conditions.append(field.endswith(str(value)))
					elif lookup == 'gt':
						conditions.append(field > value)
					elif lookup == 'gte':
						conditions.append(field >= value)
					elif lookup == 'lt':
						conditions.append(field < value)
					elif lookup == 'lte':
						conditions.append(field <= value)
					elif lookup == 'ne':
						conditions.append(field != value)
					else:
						raise ValueError(f"Unsupported lookup: {lookup}")
				else:
					field = getattr(self.model, key, None)
					if field is None:
						raise AttributeError(f"Model {self.model.__name__} has no attribute {key}")
					conditions.append(field == value)

			if conditions:
				stmt = stmt.where(and_(*conditions))

		result = await session.execute(stmt)
		return result.scalars().all()

	@with_db_session
	async def limit(self, limit: int, **kwargs: Any) -> Sequence[M]:
		"""
		Limit the amount results.

		Example:
			# Get first 10 users
			recent_users = await User.objects.limit(10)
		"""
		session = self._get_session(**kwargs)

		stmt = await self.get_queryset(**kwargs)
		stmt = stmt.limit(limit)

		result = await session.execute(stmt)
		return result.scalars().all()

	@with_db_session
	async def offset(self, offset: int, **kwargs: Any) -> Sequence[M]:
		"""
		Skip the first 'offset' results.

		Example:
			# Get users 11-20
			next_page = await User.objects.offset(10).limit(10)
		"""
		session = self._get_session(**kwargs)

		stmt = await self.get_queryset(**kwargs)
		stmt = stmt.offset(offset)

		result = await session.execute(stmt)
		return result.scalars().all()

	@with_db_session
	async def paginate(
			self,
			page: int = 1,
			per_page: int = 20,
			**kwargs: Any
	) -> PaginationResult[M]:
		"""
		Return a paginated list of objects.

		Args:
			page: Page number (1-based).
			per_page: Amount items per page.
			**kwargs: Optional filter conditions.

		Example:
			# Get first page with 10 items
			result = await User.objects.paginate(page=1, per_page=10)

			# With filters
			result = await User.objects.paginate(
				page=1,
				per_page=10,
				is_active=True,
				role='admin'
			)
		"""
		from sqlalchemy import func

		session = self._get_session(**kwargs)

		stmt = await self.get_queryset(**kwargs)

		# Get total count
		count_stmt = select(func.count()).select_from(stmt.subquery())
		total = (await session.execute(count_stmt)).scalar() or 0

		# Calculate pagination
		pages = max(1, (total + per_page - 1) // per_page)
		page = max(1, min(page, pages))
		offset = (page - 1) * per_page

		# Get paginated items
		stmt = stmt.offset(offset).limit(per_page)
		result = await session.execute(stmt)
		items = result.scalars().all()

		return PaginationResult[M](
			items=items,
			total=total,
			page=page,
			per_page=per_page,
			pages=pages
		)

	@with_db_session
	async def count(self, **kwargs: Any) -> int:
		"""
		Return the count of objects matching the given filters.

		Example:
			total_users = await User.objects.count()
			active_users = await User.objects.count(is_active=True)
		"""
		from sqlalchemy import func

		session = self._get_session(**kwargs)

		# Get base query with filters applied
		stmt = await self.get_queryset(**kwargs)
		# Convert to count query
		stmt = select(func.count()).select_from(stmt.subquery())

		result = await session.execute(stmt)
		return result.scalar() or 0

	@with_db_session
	async def filter_by_date_range(
			self,
			date_column: ColumnElement[datetime],
			start_date: datetime | None = None,
			end_date: datetime | None = None,
			order_by: ColumnElement | None = None,
			desc: bool = True,
			skip: int = 0,
			limit: int | None = None,
			**kwargs: Any
	) -> Sequence[M]:
		"""
		Filter by date range with pagination and optional ordering.

		Args:
			date_column: The column to filter on (e.g., self.model.created_at)
			start_date: Start date (inclusive)
			end_date: End date (inclusive)
			order_by: Column to order by (defaults to date_column)
			desc: Sort in descending order
			skip: Amount records to skip
			limit: Maximum amount records to return
			**kwargs: Additional filter conditions.

		Returns:
			List of filtered model instances.
		"""

		session = self._get_session(**kwargs)

		stmt = await self.get_queryset(**kwargs)

		if start_date is not None:
			stmt = stmt.filter(date_column >= start_date)

		if end_date is not None:
			stmt = stmt.filter(date_column <= end_date)

		order_column = order_by or date_column
		if order_column is not None:
			if desc:
				stmt = stmt.order_by(order_column.desc())
			else:
				stmt = stmt.order_by(order_column.asc())

		if skip > 0:
			stmt = stmt.offset(skip)

		if limit is not None:
			stmt = stmt.limit(limit)

		result = await session.execute(stmt)
		return result.scalars().all()

	@with_db_session
	async def create(self, **kwargs: Any) -> M:
		"""
		Create a new object with the given attributes.

		Args:
			**kwargs: Object attributes.

		Returns:
			The created model instance.

		Example:
			user = await User.objects.create(
				username='john',
				email='john@example.com',
				is_active=True
			)

		Raises:
			SQLAlchemyError: If there's an error during creation.
		"""
		session = self._get_session(**kwargs)

		instance = self.model(**kwargs)
		session.add(instance)

		await session.flush()
		await session.refresh(instance)
		return instance

	@with_db_session
	async def update_by_id(self, user_id: int, **kwargs: Any) -> Optional[M]:
		"""
		Update an object by its ID.

		Args:
			user_id: ID of the object to update.
			**kwargs: Attributes to update.

		Returns:
			The updated model instance or None if not found.

		Example:
			user = await User.objects.update_by_id(
				user_id=1,
				email='new@example.com',
				is_active=True
			)
		"""
		session = self._get_session(**kwargs)

		instance = await self.get(id=user_id, session=session)
		if not instance:
			return None

		for key, value in kwargs.items():
			if hasattr(instance, key) and not key.startswith('_'):
				setattr(instance, key, value)

		await session.flush()
		await session.refresh(instance)
		return instance

	@with_db_session
	async def delete_by_id(self, _id: int, **kwargs: Any) -> bool:
		"""
		Delete an object by its ID.

		Args:
			_id: ID of the object to delete.

		Returns:
			True if the object was deleted, False if not found.

		Example:
			# Delete user with ID 1
			success = await User.objects.delete_by_id(1)
		"""
		session = self._get_session(**kwargs)

		instance = await self.get(id=_id, session=session)
		if not instance:
			return False

		await session.delete(instance)
		await session.flush()
		return True

	def select_related(self, *relations: str | QueryableAttribute) -> 'BaseManager[M]':
		"""
		Optimize for foreign-key relationships.

		Args:
			*relations: String names of relationships to eager load

		Returns:
			Self for method chaining
		"""
		self._eager_loads.extend(relations)
		return self

	def prefetch_related(self, *relations: str) -> 'BaseManager[M]':
		"""
		Optimize for many-to-many and reverse foreign-key relationships.

		Args:
			*relations: String names of relationships to prefetch

		Returns:
			Self for method chaining
		"""
		if not hasattr(self, '_prefetch_loads'):
			self._prefetch_loads: list[str] = []

		self._prefetch_loads.extend(relations)
		return self

	@with_db_session
	async def bulk_create(
			self,
			objects: list[dict[str, Any]],
			return_instances: bool = False,
			**kwargs: Any
	) -> list[M] | int:
		"""
		Create multiple objects in a single query.

		Args:
			objects: List of dictionaries containing object attributes.
			return_instances: If True, return created instances (slower).

		Returns:
			List of created instances if return_instances is True, else count of created objects.

		Example:
			# Create multiple users
			users_data = [
				{'username': 'user1', 'email': 'user1@example.com'},
				{'username': 'user2', 'email': 'user2@example.com'}
			]
			created_count = await User.objects.bulk_create(users_data)
			
			# Get created instances with their IDs
			created_users = await User.objects.bulk_create(users_data, return_instances=True)
		"""
		session = self._get_session(**kwargs)

		if not objects:
			return [] if return_instances else 0

		instances = [self.model(**obj) for obj in objects]
		session.add_all(instances)
		await session.flush()

		if return_instances:
			# Refresh all instances to get their IDs
			for instance in instances:
				await session.refresh(instance)
			return instances

		return len(instances)

	@with_db_session
	async def has(self, **kwargs: Any) -> Sequence[M]:
		"""
		Return objects that have related objects matching the given filters.

		Args:
			**kwargs: Filters to apply to related objects
				Format: {relation_name={field: value}}
				Example: social_accounts={"user_id": 1}

		Returns:
			List of model instances with matching related objects

		Example:
			# Get groups that have at least one social account for user with ID 1
			groups = await SocialGroup.objects.has(social_accounts={"user_id": 1})
		"""
		from sqlalchemy.sql.expression import literal

		session = self._get_session(**kwargs)
		stmt = await self.get_queryset()

		for relation, conditions in kwargs.items():
			if not hasattr(self.model, relation):
				continue

			# Handle dictionary conditions
			if isinstance(conditions, dict):
				relationship = getattr(self.model, relation).property
				related_model = relationship.mapper.class_

				# Create correlation condition
				if relationship.direction.name == 'MANYTOONE':
					# For many-to-one, use the foreign key directly
					fk_column = getattr(self.model, f"{relation}_id")
					correlation = (fk_column == related_model.id)
				else:
					# For one-to-many or many-to-many, use the relationship's primaryjoin
					correlation = relationship.primaryjoin

				# Build conditions for the subquery
				condition_parts = [correlation]
				for key, value in conditions.items():
					if hasattr(related_model, key):
						column = getattr(related_model, key)
						if value is not None:
							condition_parts.append(column == value)

				# Create the subquery with proper correlation
				sub_query = (
					select(literal(1))
					.where(and_(*condition_parts))
					.correlate(self.model)
				)

				# Add EXISTS clause
				stmt = stmt.where(exists(sub_query))

		result = await session.execute(stmt)
		return result.scalars().all()

	def annotate(self, **annotations: Any) -> 'BaseManager[M]':
		"""
		Add annotations to the query.

		Args:
			**annotations: Annotation expressions
				(e.g., user_count=func.count(User.id))

		Returns:
			Self for method chaining

		Example:
			# Annotate groups with member count
			groups = await (
				Group.objects
				.annotate(member_count=func.count(Group.members))
				.filter(member_count__gt=5)
			)
		"""
		self._annotations.update(annotations)
		return self
