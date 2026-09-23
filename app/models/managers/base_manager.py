"""
Improved Query Manager for SQLAlchemy models.
Fixes identified issues and improves API consistency.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Any, Generic, TypeVar, Type, Optional, Sequence, cast, Callable

from fastapi import HTTPException
from sqlalchemy import ColumnElement, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload, QueryableAttribute, InstrumentedAttribute
from sqlalchemy.sql import and_, exists, select, Select

from app.core.database import async_session_maker, with_db_session
from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass
from app.schemas.common import PaginationResult

M = TypeVar("M")


@dataclass
class Prefetch:
    """Descriptor for a prefetch with optional filters and criteria."""

    path: str | QueryableAttribute[Any]
    queryset: Optional["QuerySet[Any]"] = None
    filters: Optional[dict[str, Any]] = None
    criteria: Sequence[ColumnElement[bool]] = field(default_factory=tuple)


def prefetch(
    path: str | QueryableAttribute[Any],
    *,
    queryset: Optional["QuerySet[Any]"] = None,
    filters: Optional[dict[str, Any]] = None,
    criteria: Optional[Sequence[ColumnElement[bool]]] = None,
) -> Prefetch:
    """Helper to create Prefetch descriptors with optional filters/criteria."""
    return Prefetch(path=path, queryset=queryset, filters=filters, criteria=criteria or tuple())


class QuerySet(Generic[M]):
    """
    Lazy query builder supporting method chaining and awaiting.

    All methods return new QuerySet instances, allowing full chaining:
                    users = await User.objects.filter(is_active=True).order_by(User.id.desc()).limit(10)
    """

    def __init__(
        self,
        manager: "BaseManager[M]",
        session: AsyncSession | None = None,
        *,
        criterion: Optional[list[ColumnElement[bool]]] = None,
        kw_filters: Optional[dict[str, Any]] = None,
        orderings: Optional[list[Any]] = None,
        limit_value: Optional[int] = None,
        offset_value: Optional[int] = None,
        eager_loads: Optional[list[str | QueryableAttribute[Any]]] = None,
        prefetch_loads: Optional[list[str | QueryableAttribute[Any] | Prefetch]] = None,
    ) -> None:
        self._manager = manager
        self._session = session
        self._criterion = criterion or []
        self._kw_filters = kw_filters or {}
        self._orderings = orderings or []
        self._limit_value = limit_value
        self._offset_value = offset_value
        self._eager_loads = eager_loads or []
        self._prefetch_loads = prefetch_loads or []

    def _clone(self, **overrides: Any) -> "QuerySet[M]":
        """Create a copy of this QuerySet with optional overrides."""
        criterion: list[ColumnElement[bool]] = list(
            cast(Optional[list[ColumnElement[bool]]], overrides.get("criterion", self._criterion)) or []
        )
        kw_filters: dict[str, Any] = dict(
            cast(Optional[dict[str, Any]], overrides.get("kw_filters", self._kw_filters)) or {}
        )
        orderings: list[Any] = list(cast(Optional[list[Any]], overrides.get("orderings", self._orderings)) or [])
        limit_value: Optional[int] = cast(Optional[int], overrides.get("limit_value", self._limit_value))
        offset_value: Optional[int] = cast(Optional[int], overrides.get("offset_value", self._offset_value))
        eager_loads: list[str | QueryableAttribute[Any]] = list(
            cast(
                Optional[list[str | QueryableAttribute[Any]]],
                overrides.get("eager_loads", self._eager_loads),
            )
            or []
        )
        prefetch_loads: list[str | QueryableAttribute[Any] | Prefetch] = list(
            cast(
                Optional[list[str | QueryableAttribute[Any] | Prefetch]],
                overrides.get("prefetch_loads", self._prefetch_loads),
            )
            or []
        )
        session: AsyncSession | None = cast(Optional[AsyncSession], overrides.get("session", self._session))

        return QuerySet(
            manager=self._manager,
            session=session,
            criterion=criterion,
            kw_filters=kw_filters,
            orderings=orderings,
            limit_value=limit_value,
            offset_value=offset_value,
            eager_loads=eager_loads,
            prefetch_loads=prefetch_loads,
        )

    def filter(self, *criterion: ColumnElement[bool], **kwargs: Any) -> "QuerySet[M]":
        """
        Filter queryset by criteria and/or keyword arguments.

        Examples:
                        # Using SQLAlchemy expressions
                        qs.filter(User.email.endswith('@example.com'))

                        # Using keyword arguments with lookups
                        qs.filter(email__endswith='@example.com', is_active=True)

                        # Combining both
                        qs.filter(or_(User.is_active == True, User.is_superuser == True), role_id=2)
        """
        new_criterion = list(self._criterion) + list(criterion)
        new_kw = dict(self._kw_filters)
        new_kw.update(kwargs)

        return self._clone(criterion=new_criterion, kw_filters=new_kw)

    def exclude(self, *criterion: ColumnElement[bool], **kwargs: Any) -> "QuerySet[M]":
        """
        Exclude objects matching the given criteria.

        Examples:
                        # Exclude by expression
                        qs.exclude(User.is_active == True)

                        # Exclude by keyword arguments
                        qs.exclude(role='admin', is_superuser=True)

                        # With lookups
                        qs.exclude(email__endswith='@spam.com')
        """
        # Convert criterion to negated conditions
        negated_criterion = [~c for c in criterion]

        # Convert kwargs to negated conditions
        if kwargs:
            conditions = LookupCompiler.compile_filters(self._manager.model, kwargs)
            negated_criterion.extend([~c for c in conditions])

        new_criterion = list(self._criterion) + negated_criterion
        return self._clone(criterion=new_criterion)

    def order_by(self, *clauses: Any) -> "QuerySet[M]":
        """
        Order results by the given clauses.

        Examples:
                        qs.order_by(User.created_at.desc())
                        qs.order_by(User.last_name, User.first_name)
        """
        new_orderings = list(self._orderings) + list(clauses)
        return self._clone(orderings=new_orderings)

    def limit(self, value: int) -> "QuerySet[M]":
        """Limit the amount results."""
        return self._clone(limit_value=value)

    def offset(self, value: int) -> "QuerySet[M]":
        """Skip the first 'value' results."""
        return self._clone(offset_value=value)

    def select_related(self, *relations: str | QueryableAttribute[Any]) -> "QuerySet[M]":
        """
        Optimize a query for foreign-key relationships using JOIN.

        Supports nested relationships using '__' syntax (Django-style).

        Examples:
                        qs.select_related('role')
                        qs.select_related('source', 'user')
                        qs.select_related('source__platform') # Nested relationship
                        qs.select_related('source__platform', 'user__platform') # Multiple nested
                        qs.select_related(User.role) # Using attribute directly
        """
        new_eager = list(self._eager_loads) + list(relations)
        return self._clone(eager_loads=new_eager)

    def prefetch_related(self, *relations: str | QueryableAttribute[Any] | Prefetch) -> "QuerySet[M]":
        """
        Optimize a query for many-to-many and reverse FK relationships.

        Examples:
                        qs.prefetch_related('sources')
                        qs.prefetch_related(prefetch('sources', filters={'is_active': True}))
        """
        new_prefetch = list(self._prefetch_loads) + list(relations)
        return self._clone(prefetch_loads=new_prefetch)

    def to_select(self) -> Select[tuple[M]]:
        """
        Convert QuerySet to SQLAlchemy Select statement.

        Public API for getting Select object without executing query.
        Useful for admin views, raw SQL inspection, or custom query building.

        Examples:
                        # In admin views
                        def list_query(self, request):
                                        return User.objects.filter(is_active=True).to_select()

                        # For debugging
                        stmt = User.objects.filter(is_active=True).to_select()
                        print(stmt)

                        # For custom modifications
                        stmt = User.objects.filter(is_active=True).to_select()
                        stmt = stmt.limit(100) # Additional modifications
        """
        return self._build_statement_sync()

    async def all(self) -> Sequence[M]:
        """Execute a query and return all results."""
        stmt = await self._build_statement()
        async with self._get_session() as session:
            result = await session.execute(stmt)
            return result.scalars().unique().all()

    async def first(self) -> Optional[M]:
        """Execute a query and return first result or None."""
        stmt = await self._build_statement(apply_pagination=False)
        stmt = stmt.limit(1)
        async with self._get_session() as session:
            result = await session.execute(stmt)
            return result.scalars().first()

    async def exists(self) -> bool:
        """Check if any object exists matching the filters."""
        stmt = await self._build_statement(apply_pagination=False)
        stmt = select(exists(stmt))
        async with self._get_session() as session:
            result = await session.execute(stmt)
            return result.scalar() or False

    async def count(self) -> int:
        """Return the count of objects matching the filters."""
        # Optimize count query — don't use subquery if not necessary
        stmt = select(func.count()).select_from(self._manager.model)

        # Apply only filters, not ordering or pagination
        if self._criterion:
            stmt = stmt.where(and_(*self._criterion))

        if self._kw_filters:
            conditions = LookupCompiler.compile_filters(self._manager.model, self._kw_filters)
            if conditions:
                stmt = stmt.where(and_(*conditions))

        async with self._get_session() as session:
            result = await session.execute(stmt)
            return int(result.scalar() or 0)

    async def get(self, **kwargs: Any) -> Optional[M]:
        """
        Get a single object matching the filters.
        Adds filters to existing queryset.
        """
        qs = self.filter(**kwargs)
        return await qs.first()

    async def get_or_404(self, **kwargs: Any) -> M:
        """Get a single object or raise 404."""
        obj = await self.get(**kwargs)
        if obj is None:
            raise HTTPException(status_code=404, detail=f"{self._manager.model.__name__} not found")
        return obj

    async def paginate(
        self,
        page: int = 1,
        per_page: int = 20,
    ) -> PaginationResult[M]:
        """
        Return a paginated list of objects.

        Examples:
                        result = await User.objects.filter(is_active=True).paginate(page=1, per_page=10)
        """
        # Get total count
        total = await self.count()

        # Calculate pagination
        pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, pages))
        offset = (page - 1) * per_page

        # Get paginated items
        items: list[M] = list(await self.offset(offset).limit(per_page).all())

        return PaginationResult(items=items, total=total, page=page, per_page=per_page, pages=pages)

    async def delete(self) -> Callable[[], int] | int:
        """
        Delete all objects matching the current filters.

        Returns the amount deleted objects.

        Examples:
                        # Delete inactive users
                        count = await User.objects.filter(is_active=False).delete()

                        # Delete by IDs
                        count = await AIAnalytics.objects.filter(id__in=analytics_ids).delete()

                        # Delete with complex conditions
                        count = await User.objects.filter(
                                        created_at__lt=datetime.now() - timedelta(days=365)
                        ).delete()
        """
        from sqlalchemy import delete

        # Build WHERE conditions from current filters
        conditions = []

        # Add criterion conditions
        if self._criterion:
            conditions.extend(self._criterion)

        # Add keyword filter conditions
        if self._kw_filters:
            conditions.extend(LookupCompiler.compile_filters(self._manager.model, self._kw_filters))

        # Create DELETE statement
        stmt: Any = delete(self._manager.model)
        if conditions:
            stmt = stmt.where(and_(*conditions))

        # Execute deletion
        async with self._get_session() as session:
            result: Any = await session.execute(stmt)
            await session.commit()
            return int(result.rowcount or 0)

    async def raw_sql(self, sql: str, params: list[Any] | None = None) -> Sequence[M]:
        """
        Execute raw SQL query and return model instances.

        This method allows executing custom SQL queries while still returning properly
        mapped SQLAlchemy model instances. The SQL should select columns that match
        the model's table structure.

        Args:
                        sql: Raw SQL query string with parameter placeholders
                        params: List of parameters to substitute in the query

        Returns:
                        Sequence of model instances

        Examples:
                        # Basic raw SQL with parameters
                        users = await User.objects.raw_sql(
                                        "SELECT * FROM users WHERE is_active = %s AND created_at > %s",
                                        [True, '2024-01-01']
                        )

                        # PostgreSQL array operations
                        analytics = await AIAnalytics.objects.raw_sql('''
                                        SELECT *
                                        FROM ai_analytics
                                        WHERE source_id = %s
                                        AND main_topics && %s
                                        AND main_topics IS NOT NULL
                                        LIMIT 1
                        ''', [source_id, topics])

        Note:
                        — Use %s as parameter placeholders (works with both PostgreSQL and MySQL)
                        — The query should return all columns needed to populate model instances
                        — For complex queries that can't be expressed with the QuerySet API
        """
        if params is None:
            params = []

        # Convert to dictionary parameters for SQLAlchemy
        # Assuming params are positional, convert to named parameters: %s -> :param_0, :param_1, etc.
        param_dict = {f"param_{i}": value for i, value in enumerate(params)}

        # Replace %s with named parameters
        formatted_sql = sql
        for i in range(len(params)):
            formatted_sql = formatted_sql.replace("%s", f":param_{i}", 1)

        # Use current session or create temporary one
        if self._session is not None:
            result = await self._session.execute(text(formatted_sql), param_dict)
            return result.scalars().all()
        else:
            async with async_session_maker() as session:
                result = await session.execute(text(formatted_sql), param_dict)
                await session.commit()
                return result.scalars().all()

    @asynccontextmanager
    async def _get_session(self):
        """Get session from an instance or create a new one."""
        if self._session is not None:
            yield self._session
        else:
            async with async_session_maker() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

    def __await__(self):
        """Allow awaiting QuerySet directly."""
        return self.all().__await__()

    def _build_statement_sync(self, *, apply_pagination: bool = True) -> Select[tuple[M]]:
        """
        Build the SQLAlchemy Select statement from accumulated parameters (sync version).

        This is a synchronous version for use in admin views where async is not supported.
        """
        stmt = select(self._manager.model)

        # Apply a criterion (expressions)
        if self._criterion:
            stmt = stmt.where(and_(*self._criterion))

        # Apply keyword filters
        if self._kw_filters:
            conditions = LookupCompiler.compile_filters(self._manager.model, self._kw_filters)
            if conditions:
                stmt = stmt.where(and_(*conditions))

        # Apply eager loading (joinedload)
        if self._eager_loads:
            for rel in self._eager_loads:
                if isinstance(rel, str):
                    # Support nested relationships with '__' syntax (Django-style)
                    # e.g., "source__platform" → joinedload(Model.source).joinedload(Source.platform)
                    if "__" in rel:
                        parts = rel.split("__")
                        current_model: Any = self._manager.model
                        first_rel = getattr(current_model, parts[0], None)
                        if first_rel is None:
                            continue
                        first_attr = cast(QueryableAttribute[Any], first_rel)
                        join_option = joinedload(first_attr)
                        if hasattr(first_attr.property, "mapper"):
                            current_model = first_attr.property.mapper.class_

                        for part in parts[1:]:
                            next_rel = getattr(current_model, part, None)
                            if next_rel is None:
                                break
                            next_attr = cast(QueryableAttribute[Any], next_rel)
                            join_option = join_option.joinedload(next_attr)
                            if hasattr(next_attr.property, "mapper"):
                                current_model = next_attr.property.mapper.class_

                        stmt = stmt.options(join_option)
                    else:
                        # Simple relationship name
                        rel_attr = cast(QueryableAttribute[Any], getattr(self._manager.model, rel, None))
                        if rel_attr is not None:
                            stmt = stmt.options(joinedload(rel_attr))
                elif isinstance(rel, (QueryableAttribute, InstrumentedAttribute)):
                    stmt = stmt.options(joinedload(rel))

        # Apply prefetch loading (selectinload)
        if self._prefetch_loads:
            for pref in self._prefetch_loads:
                pref_option = self._manager.build_prefetch_option(pref)
                if pref_option is not None:
                    stmt = stmt.options(pref_option)

        # Apply ordering
        if self._orderings:
            stmt = stmt.order_by(*self._orderings)

        # Apply pagination
        if apply_pagination:
            if self._offset_value is not None:
                stmt = stmt.offset(self._offset_value)
            if self._limit_value is not None:
                stmt = stmt.limit(self._limit_value)

        return stmt

    async def _build_statement(self, *, apply_pagination: bool = True) -> Select[tuple[M]]:
        """Build the SQLAlchemy Select statement (async version for compatibility)."""
        return self._build_statement_sync(apply_pagination=apply_pagination)

    @property
    def criterion(self) -> list[ColumnElement[bool]]:
        return self._criterion

    @property
    def kw_filters(self) -> dict[str, Any]:
        return self._kw_filters


class BaseManager(Generic[M]):
    """
    Async manager providing query methods for SQLAlchemy models.

    All query methods return QuerySet for chaining.
    Use await to execute the query.

    Usage:
                    # Simple queries
                    users = await User.objects.all()
                    user = await User.objects.get(id=1)

                    # Chaining
                    users = await User.objects.filter(is_active=True).order_by(User.id.desc()).limit(10)

                    # With relations
                    users = await User.objects.select_related('role').filter(is_active=True)

                    # Pagination
                    result = await User.objects.filter(is_active=True).paginate(page=1, per_page=20)
    """

    def __init__(self, model: Type[M] | None = None) -> None:
        self._model: Type[M] | None = model

    @property
    def model(self) -> Type[M]:
        """The managed model class (set via __init__ or lazily by __get__)."""
        if self._model is None:
            raise AttributeError("no model set for {0}".format(self.__class__.__name__))
        return self._model

    def __get__(self, instance: Any, objtype: Type[Any] | None = None) -> "BaseManager[M]":
        """Support for using the manager as a class attribute."""
        if instance is not None:
            owner = objtype.__name__ if objtype is not None else self.__class__.__name__
            raise AttributeError("Manager isn't accessible via %s instances" % owner)

        if self._model is None and objtype is not None:
            self._model = cast(Type[M], objtype)

        return self

    def get_queryset(self, session: AsyncSession | None = None) -> QuerySet[M]:
        """Create a new QuerySet instance, tenant-guarded for scoped models."""
        return QuerySet(manager=self, session=session, criterion=self._tenant_criterion())

    def _tenant_scoped(self) -> bool:
        """True if the model is owned by a tenant (see `TenantScopedMixin`)."""
        return bool(getattr(self.model, "__tenant_scoped__", False))

    def _tenant_criterion(self) -> list[ColumnElement[bool]]:
        """
        Tenant guard for scoped models.

        Every read path goes through `get_queryset`, so this is the single place
        that makes cross-tenant rows unreachable: the condition is baked into
        the QuerySet, and `update_by_id`/`delete_by_id`/`count`/`delete` all
        inherit it. `raw_sql` is the documented exception (it is raw SQL).
        """
        if not self._tenant_scoped() or is_bypass():
            return []

        tenant_id = current_tenant_id()
        if tenant_id is None:
            raise TenantContextError(
                f"{self.model.__name__} is tenant-scoped but no tenant is in context; "
                f"use app.core.tenant_context.tenant_scope() or an explicit bypass"
            )
        return [cast(Any, self.model).tenant_id == tenant_id]

    @staticmethod
    async def _default_tenant_id(session: AsyncSession) -> int:
        """Bootstrap workspace id: where superuser writes land without a context."""
        from sqlalchemy import select

        from app.core.config import settings
        from app.models.tenant import Tenant

        stmt = select(Tenant.id).where(Tenant.slug == settings.DEFAULT_TENANT_SLUG)
        tenant_id = (await session.execute(stmt)).scalar_one_or_none()
        if tenant_id is None:
            raise TenantContextError(
                f"Tenant {settings.DEFAULT_TENANT_SLUG!r} is missing — run `alembic upgrade head` "
                f"or create the bootstrap tenant before writing tenant-scoped rows"
            )
        return int(tenant_id)

    async def _apply_tenant(self, session: AsyncSession, kwargs: dict[str, Any]) -> dict[str, Any]:
        """
        Stamp `tenant_id` on a row about to be created (fail-closed).

        - normal call: takes the tenant from context, rejects a different one;
        - superuser call: honours an explicit `tenant_id`, else falls back to the
          bootstrap tenant (owner CLI/legacy admin writing in its own workspace).
        """
        if not self._tenant_scoped():
            return kwargs

        explicit = kwargs.pop("tenant_id", None)

        if is_bypass():
            kwargs["tenant_id"] = explicit if explicit is not None else await self._default_tenant_id(session)
            return kwargs

        tenant_id = current_tenant_id()
        if tenant_id is None:
            raise TenantContextError(
                f"Cannot create {self.model.__name__} without a tenant in context; "
                f"use app.core.tenant_context.tenant_scope()"
            )
        if explicit is not None and explicit != tenant_id:
            raise TenantContextError(
                f"Refusing to create {self.model.__name__} for tenant {explicit} "
                f"while running as tenant {tenant_id}"
            )
        kwargs["tenant_id"] = tenant_id
        return kwargs

    # Query methods returning QuerySet

    def all(self, session: AsyncSession | None = None) -> QuerySet[M]:
        """
        Return all instances of the model.

        Examples:
                        users = await User.objects.all()
        """
        return self.get_queryset(session)

    def filter(
        self, *criterion: ColumnElement[bool], session: AsyncSession | None = None, **kwargs: Any
    ) -> QuerySet[M]:
        """
        Filter objects by criteria.

        Examples:
                        # Expression-based
                        users = await User.objects.filter(User.is_active == True)

                        # Keyword arguments
                        users = await User.objects.filter(is_active=True, role_id=2)

                        # With lookups
                        users = await User.objects.filter(email__endswith='@example.com')

                        # Chaining
                        users = await User.objects.filter(is_active=True).filter(role_id=2)
        """
        return self.get_queryset(session).filter(*criterion, **kwargs)

    def exclude(
        self, *criterion: ColumnElement[bool], session: AsyncSession | None = None, **kwargs: Any
    ) -> QuerySet[M]:
        """
        Exclude objects matching the criteria.

        Examples:
                        non_admins = await User.objects.exclude(role='admin')
                        active_non_superusers = await User.objects.filter(is_active=True).exclude(is_superuser=True)
        """
        return self.get_queryset(session).exclude(*criterion, **kwargs)

    def order_by(self, *clauses: Any, session: AsyncSession | None = None) -> QuerySet[M]:
        """
        Order results by the given clauses.

        Examples:
                        users = await User.objects.order_by(User.created_at.desc())
        """
        return self.get_queryset(session).order_by(*clauses)

    def select_related(
        self, *relations: str | QueryableAttribute[Any], session: AsyncSession | None = None
    ) -> QuerySet[M]:
        """
        Optimize for foreign-key relationships.

        Examples:
                        users = await User.objects.select_related('role').all()
        """
        return self.get_queryset(session).select_related(*relations)

    def prefetch_related(
        self, *relations: str | QueryableAttribute[Any] | Prefetch, session: AsyncSession | None = None
    ) -> QuerySet[M]:
        """
        Optimize for many-to-many and reverse foreign-key relationships.

        Examples:
                        platforms = await Platform.objects.prefetch_related('sources').all()
                        platforms = await Platform.objects.prefetch_related(
                                        prefetch('sources', filters={'is_active': True})
                        ).all()
        """
        return self.get_queryset(session).prefetch_related(*relations)

    # Direct execution methods

    async def get(self, session: AsyncSession | None = None, **kwargs: Any) -> Optional[M]:
        """
        Get a single object matching the filters or None.

        Examples:
                        user = await User.objects.get(id=1)
                        user = await User.objects.get(email="user@example.com")
        """
        return await self.get_queryset(session).get(**kwargs)

    async def get_or_404(self, session: AsyncSession | None = None, **kwargs: Any) -> M:
        """
        Get a single object or raise 404.

        Examples:
                        user = await User.objects.get_or_404(id=1)
        """
        return await self.get_queryset(session).get_or_404(**kwargs)

    async def exists(self, session: AsyncSession | None = None, **kwargs: Any) -> bool:
        """
        Check if any object exists matching the filters.

        Examples:
                        exists = await User.objects.exists(username='admin')
        """
        return await self.get_queryset(session).filter(**kwargs).exists()

    async def count(self, session: AsyncSession | None = None, **kwargs: Any) -> int:
        """
        Return the count of objects matching the filters.

        Examples:
                        total = await User.objects.count()
                        active_count = await User.objects.count(is_active=True)
        """
        return await self.get_queryset(session).filter(**kwargs).count()

    # CRUD methods

    @with_db_session
    async def create(self, session: AsyncSession, **kwargs: Any) -> M:
        """
        Create a new object with the given attributes.

        Examples:
                        user = await User.objects.create(
                                        username='john',
                                        email='john@example.com',
                                        is_active=True
                        )
        """
        kwargs = await self._apply_tenant(session, kwargs)
        instance = self.model(**kwargs)
        session.add(instance)
        await session.flush()
        await session.refresh(instance)
        return instance

    @with_db_session
    async def bulk_create(
        self,
        objects: list[dict[str, Any]],
        session: AsyncSession,
        return_instances: bool = False,
    ) -> list[M] | int:
        """
        Create multiple objects in a single query.

        Examples:
                        users_data = [
                                        {'username': 'user1', 'email': 'user1@example.com'},
                                        {'username': 'user2', 'email': 'user2@example.com'}
                        ]
                        count = await User.objects.bulk_create(users_data)
                        users = await User.objects.bulk_create(users_data, return_instances=True)
        """
        if not objects:
            return [] if return_instances else 0

        stamped = [await self._apply_tenant(session, dict(obj)) for obj in objects]
        instances = [self.model(**obj) for obj in stamped]
        session.add_all(instances)
        await session.flush()

        if return_instances:
            for instance in instances:
                await session.refresh(instance)
            return instances

        return len(instances)

    async def delete(self, session: AsyncSession | None = None, **filters: Any) -> Callable[[], int] | int:
        """
        Delete objects matching the given filters.

        Examples:
                        # Delete by filters
                        count = await User.objects.delete(is_active=False)

                        # Delete all (be careful!)
                        count = await User.objects.delete()
        """
        return await self.get_queryset(session).filter(**filters).delete()

    @with_db_session
    async def update_by_id(self, instance_id: int, session: AsyncSession, **kwargs: Any) -> Optional[M]:
        """
        Update an object by its ID.

        Examples:
                        user = await User.objects.update_by_id(
                                        user_id=1,
                                        email='new@example.com'
                        )
        """
        instance = await self.get(id=instance_id, session=session)
        if not instance:
            return None

        for key, value in kwargs.items():
            if hasattr(instance, key) and not key.startswith("_"):
                setattr(instance, key, value)

        await session.flush()
        await session.refresh(instance)
        return instance

    @with_db_session
    async def delete_by_id(self, instance_id: int, session: AsyncSession) -> bool:
        """
        Delete an object by its ID.

        Examples:
                        success = await User.objects.delete_by_id(1)
        """
        instance = await self.get(id=instance_id, session=session)
        if not instance:
            return False

        await session.delete(instance)
        await session.flush()
        return True

    # Advanced query methods

    async def filter_by_date_range(
        self,
        date_column: ColumnElement[datetime],
        start_date: date | None = None,
        end_date: date | None = None,
        order_by: ColumnElement[Any] | None = None,
        desc: bool = True,
        skip: int = 0,
        limit: int | None = None,
        session: AsyncSession | None = None,
    ) -> Sequence[M]:
        """
        Filter by date range with pagination and optional ordering.

        Examples:
                        from datetime import datetime, timedelta

                        start = datetime.now() — timedelta(days=7)
                        end = datetime.now()

                        recent = await User.objects.filter_by_date_range(
                                        User.created_at,
                                        start_date=start,
                                        end_date=end,
                                        limit=10
                        )
        """
        qs = self.get_queryset(session)

        if start_date is not None:
            qs = qs.filter(date_column >= start_date)

        if end_date is not None:
            qs = qs.filter(date_column <= end_date)

        order_column = order_by or date_column
        if order_column is not None:
            qs = qs.order_by(order_column.desc() if desc else order_column.asc())

        if skip > 0:
            qs = qs.offset(skip)

        if limit is not None:
            qs = qs.limit(limit)

        return await qs.all()

    async def paginate(
        self, page: int = 1, per_page: int = 20, session: AsyncSession | None = None, **filters: Any
    ) -> PaginationResult[M]:
        """
        Return a paginated list of objects.

        Examples:
                        result = await User.objects.paginate(page=1, per_page=10, is_active=True)
        """
        return await self.get_queryset(session).filter(**filters).paginate(page=page, per_page=per_page)

    async def raw_sql(
        self, sql: str, params: list[Any] | None = None, session: AsyncSession | None = None
    ) -> Sequence[M]:
        """
        Execute raw SQL query from manager.
        """
        return await self.get_queryset(session).raw_sql(sql, params)

    # Helper methods for building queries
    def build_prefetch_option(self, relation: str | QueryableAttribute[Any] | InstrumentedAttribute[Any] | Prefetch):
        """Create selectinload option for a relation or Prefetch descriptor."""
        if isinstance(relation, Prefetch):
            return self.build_prefetch_from_descriptor(relation)

        if isinstance(relation, (QueryableAttribute, InstrumentedAttribute)):
            return selectinload(relation)

        if isinstance(relation, str):
            if "." in relation:
                return self.build_nested_selectinload(self.model, relation)
            rel_attr = cast(QueryableAttribute[Any], getattr(self.model, relation, None))
            if rel_attr is not None and hasattr(rel_attr, "property"):
                return selectinload(rel_attr)

        return None

    def build_prefetch_from_descriptor(self, descriptor: Prefetch):
        """Build selectinload with filters from Prefetch descriptor."""
        path = descriptor.path
        filters = descriptor.filters or {}
        criteria = list(descriptor.criteria)
        queryset = descriptor.queryset

        if isinstance(path, (QueryableAttribute, InstrumentedAttribute)):
            attr = path
            mapper = cast(Type[Any], attr.property.mapper.class_)
        elif isinstance(path, str):
            if "." in path:
                if filters or criteria or queryset:
                    raise ValueError("Filtered prefetch is not supported for dotted relation paths")
                return self.build_nested_selectinload(self.model, path)
            path_attr = getattr(self.model, path, None)
            if path_attr is None:
                return None
            attr = cast(QueryableAttribute[Any], path_attr)
            mapper = cast(Type[Any], attr.property.mapper.class_)
        else:
            return None

        # Build conditions from different sources
        conditions: list[ColumnElement[bool]] = []

        # 1. From queryset (Django-style)
        if queryset is not None:
            if queryset.criterion:
                conditions.extend(queryset.criterion)
            if queryset.kw_filters:
                conditions.extend(LookupCompiler.compile_filters(mapper, queryset.kw_filters))

        # 2. From filters
        if filters:
            conditions.extend(LookupCompiler.compile_filters(mapper, filters))

        # 3. From criteria
        conditions.extend(criteria)

        if conditions:
            prefetch_attr = attr.and_(*conditions)
            return selectinload(prefetch_attr)

        return selectinload(attr)

    @staticmethod
    def build_nested_selectinload(model: Type[M], path: str):
        """Build nested selectinload for dotted paths like 'role.permissions'."""
        parts = path.split(".")
        first_attr = getattr(model, parts[0], None)
        if first_attr is None:
            return None
        attr = cast(QueryableAttribute[Any], first_attr)
        if not hasattr(attr, "property"):
            return None

        option = selectinload(attr)
        current_cls = cast(Type[Any], attr.property.mapper.class_)

        for part in parts[1:]:
            next_attr = getattr(current_cls, part, None)
            if next_attr is None:
                return option
            nested_attr = cast(QueryableAttribute[Any], next_attr)
            option = option.selectinload(nested_attr)
            current_cls = cast(Type[Any], nested_attr.property.mapper.class_)

        return option


class LookupCompiler:
    """Компилятор lookup-выражений в SQLAlchemy условия."""

    @staticmethod
    def compile_lookup(model: Type[Any], key: str, value: Any) -> ColumnElement[bool]:
        """
        Компилирует lookup выражение в SQLAlchemy условие.

        Поддерживаемые lookups:
        - field (равенство)
        - field__in (вхождение в список)
        - field__isnull (проверка на NULL)
        - field__contains (подстрока)
        - field__icontains (подстрока без учета регистра)
        - field__startswith (начинается с)
        - field__endswith (заканчивается на)
        - field__gt (больше)
        - field__gte (больше или равно)
        - field__lt (меньше)
        - field__lte (меньше или равно)
        - field__ne (не равно)
        """
        if "__" in key:
            field_name, lookup = key.rsplit("__", 1)
            f = getattr(model, field_name, None)
            if f is None:
                raise AttributeError(f"Model {model.__name__} has no attribute '{field_name}'")

            return LookupCompiler._apply_lookup(f, lookup, value)
        else:
            f = getattr(model, key, None)
            if f is None:
                raise AttributeError(f"Model {model.__name__} has no attribute '{key}'")
            return f == value

    @staticmethod
    def _apply_lookup(f: Any, lookup: str, value: Any) -> ColumnElement[bool]:
        """Применяет конкретный lookup к полю."""
        if lookup == "in":
            if not isinstance(value, (list, tuple, set)):
                raise ValueError(f"'in' lookup requires list, tuple or set, got {type(value).__name__}")
            return cast(ColumnElement[bool], f.in_(value))

        elif lookup == "isnull":
            return cast(ColumnElement[bool], f.is_(None) if value else f.is_not(None))

        elif lookup == "contains":
            return cast(ColumnElement[bool], f.contains(str(value)))

        elif lookup == "icontains":
            return cast(ColumnElement[bool], f.ilike(f"%{value}%"))

        elif lookup == "startswith":
            return cast(ColumnElement[bool], f.startswith(str(value)))

        elif lookup == "endswith":
            return cast(ColumnElement[bool], f.endswith(str(value)))

        elif lookup == "gt":
            return f > value

        elif lookup == "gte":
            return f >= value

        elif lookup == "lt":
            return f < value

        elif lookup == "lte":
            return f <= value

        elif lookup == "ne":
            return f != value

        else:
            raise ValueError(f"Unsupported lookup: {lookup}")

    @staticmethod
    def compile_filters(model: Type[Any], filters: dict[str, Any]) -> list[ColumnElement[bool]]:
        """Компилирует словарь фильтров в список условий."""
        return [LookupCompiler.compile_lookup(model, key, value) for key, value in filters.items()]
