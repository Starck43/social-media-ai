"""
Improved Query Manager for SQLAlchemy models.
Fixes identified issues and improves API consistency.
"""

from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any, Callable, Generic, Optional, Sequence, Type, TypeVar, cast

from fastapi import HTTPException
from sqlalchemy import ColumnElement, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import QueryableAttribute
from sqlalchemy.sql import Select, and_, exists, select

from app.core.database import async_session_maker, with_db_session
from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass
from app.schemas.common import PaginationResult

from .lookup_compiler import LookupCompiler
from .query_compilation import QueryCompilationMixin
from .relation_loading import Prefetch, RelationLoadingMixin, prefetch, relation_path_parts

M = TypeVar("M")


class QuerySet(QueryCompilationMixin, Generic[M]):
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
        join_specs: Optional[list[tuple[Any, Any, Any]]] = None,
        group_by_clauses: Optional[list[Any]] = None,
        havings: Optional[list[ColumnElement[bool]]] = None,
        select_columns: Optional[Sequence[Any]] = None,
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
        # (target, onclause, is_outer) triples, applied in order before the
        # where-clause so a join may reference columns from earlier joins.
        self._join_specs: list[tuple[Any, Any, bool]] = list(join_specs or [])
        self._group_by_clauses: list[Any] = list(group_by_clauses or [])
        self._havings: list[ColumnElement[bool]] = list(havings or [])
        # When set, the SELECT list is replaced with these columns/labelled
        # aggregates instead of whole model instances (aggregate/rollup reads).
        self._select_columns: Optional[Sequence[Any]] = list(select_columns) if select_columns is not None else None

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
        join_specs: list[tuple[Any, Any, bool]] = list(
            cast(Optional[list[tuple[Any, Any, bool]]], overrides.get("join_specs", self._join_specs)) or []
        )
        group_by_clauses: list[Any] = list(
            cast(Optional[list[Any]], overrides.get("group_by_clauses", self._group_by_clauses)) or []
        )
        havings: list[ColumnElement[bool]] = list(
            cast(Optional[list[ColumnElement[bool]]], overrides.get("havings", self._havings)) or []
        )
        select_columns: Optional[Sequence[Any]] = cast(
            Optional[Sequence[Any]], overrides.get("select_columns", self._select_columns)
        )

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
            join_specs=join_specs,
            group_by_clauses=group_by_clauses,
            havings=havings,
            select_columns=select_columns,
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

    def join(
        self,
        *targets: Any,
        onclause: Optional[ColumnElement[bool]] = None,
        is_outer: bool = False,
    ) -> "QuerySet[M]":
        """Add an explicit JOIN so related rows can be filtered/aggregated.

        Without it a read that needs another table had to open its own session
        and hand-build `select(...).join(...)` — which is exactly how callers
        slipped past the tenant guard. Chained joins apply in order.

        `onclause` may be passed either by keyword or positionally as the second
        argument (Django/SQLAlchemy style).

        Examples:
                        qs.join(Source, AIAnalytics.source_id == Source.id)
                        qs.join(Source, Source.tenant_id == AIAnalytics.tenant_id)
        """
        targets, onclause = self._split_join_args(targets, onclause)
        specs = list(self._join_specs) + [(target, onclause, is_outer) for target in targets]
        return self._clone(join_specs=specs)

    @staticmethod
    def _split_join_args(
        targets: tuple[Any, ...], onclause: Optional[ColumnElement[bool]]
    ) -> tuple[tuple[Any, ...], Optional[ColumnElement[bool]]]:
        """Allow `join(Target, cond)` alongside `join(Target, onclause=cond)`."""
        if onclause is None and len(targets) == 2 and isinstance(targets[1], ColumnElement):
            return (targets[0],), cast(ColumnElement[bool], targets[1])
        return targets, onclause

    def outerjoin(self, *targets: Any, onclause: Optional[ColumnElement[bool]] = None) -> "QuerySet[M]":
        """Add a LEFT OUTER JOIN — keeps parent rows without a match (rollups)."""
        return self.join(*targets, onclause=onclause, is_outer=True)

    def group_by(self, *clauses: Any) -> "QuerySet[M]":
        """Group rows, normally combined with `select(func.count(...))`."""
        return self._clone(group_by_clauses=list(self._group_by_clauses) + list(clauses))

    def having(self, *criterion: ColumnElement[bool]) -> "QuerySet[M]":
        """Filter grouped rows (aggregate conditions)."""
        return self._clone(havings=list(self._havings) + list(criterion))

    def values(self, *columns: Any) -> "QuerySet[M]":
        """Select explicit columns/aggregates instead of whole model rows.

        Read the result with `rows()` / `first_row()` / `scalar()`.

        Examples:
                        total = await Source.objects.filter(is_active=True).values(func.count(Source.id)).scalar()
        """
        return self._clone(select_columns=list(columns))

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

    async def rows(self) -> Sequence[Any]:
        """Execute a `values()`-style query and return raw `Row` tuples.

        The aggregate/rollup read path: joins, `group_by` and labelled
        aggregates live on the queryset, so the caller never needs a session.
        """
        stmt = await self._build_statement()
        async with self._get_session() as session:
            result = await session.execute(stmt)
            return result.all()

    async def first_row(self) -> Optional[Any]:
        """Execute a `values()`-style query and return the first `Row`, or None."""
        stmt = await self._build_statement(apply_pagination=False)
        stmt = stmt.limit(1)
        async with self._get_session() as session:
            result = await session.execute(stmt)
            return result.first()

    async def scalar(self, default: Any = None) -> Any:
        """First column of the first row (aggregate reads), or `default`."""
        row = await self.first_row()
        if row is None:
            return default
        try:
            value = row[0]
        except (IndexError, TypeError):
            return default
        return default if value is None else value

    async def update(self, **values: Any) -> int:
        """Bulk UPDATE every row matching the current filters. Returns the count.

        A single statement instead of loading rows and re-saving them; the
        tenant guard and all `filter()` criteria apply unchanged.
        """
        from sqlalchemy import update as sa_update

        if "tenant_id" in values and getattr(self._manager.model, "__tenant_scoped__", False):
            raise ValueError("Workspace ownership is immutable")
        if getattr(self._manager.model, "__personal_scoped__", False) and set(values) & {
            "owner_user_id",
            "provider_id",
            "is_default",
        }:
            raise ValueError("Use the validated personal catalog service")
        conditions: list[ColumnElement[bool]] = list(self._criterion)
        if self._kw_filters:
            conditions.extend(LookupCompiler.compile_filters(self._manager.model, self._kw_filters))
        if self._join_specs:
            raise ValueError("update() does not support joins — filter without them")

        stmt: Any = sa_update(self._manager.model)
        if conditions:
            stmt = stmt.where(and_(*conditions))

        async with self._get_session() as session:
            result: Any = await session.execute(stmt.values(**values))
            await session.commit()
            return int(result.rowcount or 0)

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

    async def _build_statement(self, *, apply_pagination: bool = True) -> Select[tuple[M]]:
        """Build the SQLAlchemy Select statement (async version for compatibility)."""
        return self._build_statement_sync(apply_pagination=apply_pagination)

    @property
    def criterion(self) -> list[ColumnElement[bool]]:
        return self._criterion

    @property
    def kw_filters(self) -> dict[str, Any]:
        return self._kw_filters


class BaseManager(RelationLoadingMixin, Generic[M]):
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
        return QuerySet(manager=self, session=session, criterion=self._tenant_criterion() + self._personal_criterion())

    def _personal_criterion(self):
        """The signed-in user's catalog, whoever they are — owner included.

        A personal catalog is not a workspace-wide fleet, so the platform
        bypass must not widen the read: the owner sees their own providers and
        models, an admin theirs. The bypass still matters on writes
        (`_personal_values`), where it means "the actor names the owner".
        """
        if not getattr(self.model, "__personal_scoped__", False):
            return []
        from app.core.personal_catalog_context import current_catalog_owner_id

        return [self.model.owner_user_id == current_catalog_owner_id()]

    def _personal_values(self, values):
        if not getattr(self.model, "__personal_scoped__", False):
            return values
        from app.core.personal_catalog_context import current_catalog_owner_id

        if values.get("is_default"):
            raise ValueError("Use the atomic personal default service")
        explicit = values.get("owner_user_id")
        if is_bypass():
            if type(explicit) is not int or explicit <= 0:
                raise PermissionError("Operator writes require an explicit catalog owner")
        else:
            owner = current_catalog_owner_id()
            if explicit is not None and explicit != owner:
                raise PermissionError("Foreign personal catalog owner")
            values["owner_user_id"] = owner
        return values

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
        from app.models.tenancy.tenant import Tenant

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

        A superuser write that takes its tenant from context is not a choice
        between workspaces: the caller is already running as one and simply
        omitted the column. Requiring it twice made every such write land in the
        bootstrap workspace instead of the one the request had selected — the
        row was created and then invisible to the scoped read that followed.
        """
        if not self._tenant_scoped():
            return kwargs

        explicit = kwargs.pop("tenant_id", None)

        if is_bypass():
            tenant_id = explicit if explicit is not None else current_tenant_id()
            if tenant_id is None:
                tenant_id = await self._default_tenant_id(session)
            kwargs["tenant_id"] = tenant_id
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

    def join(
        self, *targets: Any, onclause: Optional[ColumnElement[bool]] = None, is_outer: bool = False, **kwargs: Any
    ) -> QuerySet[M]:
        """Start a queryset with an explicit JOIN (see `QuerySet.join`).

        Examples:
                        await Source.objects.outerjoin(Task, ...).values(...).rows()
        """
        return self.get_queryset(kwargs.pop("session", None)).join(*targets, onclause=onclause, is_outer=is_outer)

    def outerjoin(self, *targets: Any, onclause: Optional[ColumnElement[bool]] = None, **kwargs: Any) -> QuerySet[M]:
        """Start a queryset with a LEFT OUTER JOIN (see `QuerySet.outerjoin`)."""
        return self.get_queryset(kwargs.pop("session", None)).outerjoin(*targets, onclause=onclause)

    def group_by(self, *clauses: Any, **kwargs: Any) -> QuerySet[M]:
        """Start a grouped queryset (see `QuerySet.group_by`)."""
        return self.get_queryset(kwargs.pop("session", None)).group_by(*clauses)

    def values(self, *columns: Any, **kwargs: Any) -> QuerySet[M]:
        """Start a queryset with an explicit column/aggregate list (see `QuerySet.values`)."""
        return self.get_queryset(kwargs.pop("session", None)).values(*columns)

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
        kwargs = self._personal_values(await self._apply_tenant(session, kwargs))
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

        stamped = [self._personal_values(await self._apply_tenant(session, dict(obj))) for obj in objects]
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

        if getattr(self.model, "__personal_scoped__", False):
            if "is_default" in kwargs:
                raise ValueError("Use the atomic personal default service")
            for key in ("owner_user_id", "provider_id"):
                if key in kwargs and kwargs[key] != getattr(instance, key, None):
                    raise ValueError("Catalog ownership and provider are immutable")
        if self._tenant_scoped() and "tenant_id" in kwargs and kwargs["tenant_id"] != instance.tenant_id:
            raise ValueError("Workspace ownership is immutable")
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

    async def aggregate_rows(self, stmt: Any, session: AsyncSession | None = None) -> Sequence[Any]:
        """Run a `Select` built by the caller and return raw `Row` tuples.

        The escape hatch for association tables, which have columns but no
        model to hang a queryset on. Model reads must go through
        `filter()/values()` instead so the tenant guard still applies.
        """
        async with self._get_owned_session(session) as db:
            result = await db.execute(stmt)
            return result.all()

    # Association-table helpers

    async def _replace_secondary(
        self,
        table: Any,
        key_column: str,
        key_value: Any,
        target_column: str,
        target_values: Sequence[Any],
        session: AsyncSession | None = None,
    ) -> int:
        """Rewrite a secondary (m2m) association table for one owner row.

        Association tables have no model, so they cannot go through the
        queryset; this is the single place that writes them. Callers used to
        open their own session and hand-roll delete+insert-on-conflict, which
        duplicated the same logic in three places.
        """
        from sqlalchemy import delete as sa_delete
        from sqlalchemy import insert as sa_insert

        async with self._get_owned_session(session) as db:
            await db.execute(sa_delete(table).where(table.c[key_column] == key_value))
            values = sorted({int(v) for v in target_values})
            if values:
                await db.execute(
                    sa_insert(table),
                    [{key_column: key_value, target_column: v} for v in values],
                )
            await db.commit()
            return len(values)

    async def _add_secondary(
        self,
        table: Any,
        key_column: str,
        key_value: Any,
        target_column: str,
        target_values: Sequence[Any],
        session: AsyncSession | None = None,
    ) -> int:
        """Add links to a secondary table, skipping pairs that already exist."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        values = sorted({int(v) for v in target_values})
        if not values:
            return 0

        async with self._get_owned_session(session) as db:
            await db.execute(
                pg_insert(table)
                .values([{key_column: key_value, target_column: v} for v in values])
                .on_conflict_do_nothing()
            )
            await db.commit()
            return len(values)

    @asynccontextmanager
    async def _get_owned_session(self, session: AsyncSession | None = None):
        """Yield `session` when given, otherwise a session that is committed."""
        if session is not None:
            yield session
        else:
            async with async_session_maker() as owned:
                try:
                    yield owned
                    await owned.commit()
                except Exception:
                    await owned.rollback()
                    raise

    # Helper methods for building queries
