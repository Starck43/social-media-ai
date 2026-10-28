"""Translate accumulated QuerySet state into a SQLAlchemy Select (no I/O)."""

from typing import TypeVar

from sqlalchemy.sql import Select, and_, select

from .lookup_compiler import LookupCompiler

M = TypeVar("M")


class QueryCompilationMixin:
    """Statement construction only; execution and session ownership stay in QuerySet."""

    def _build_statement_sync(self, *, apply_pagination: bool = True) -> Select[tuple[M]]:
        """
        Build the SQLAlchemy Select statement from accumulated parameters (sync version).

        This is a synchronous version for use in admin views where async is not supported.
        """
        if self._select_columns is not None:
            stmt = select(*self._select_columns)
        else:
            stmt = select(self._manager.model)

        # Apply explicit joins first, so the where-clause may reference columns
        # coming from a joined table.
        for target, onclause, is_outer in self._join_specs:
            if onclause is not None:
                stmt = stmt.join(target, onclause, isouter=is_outer)
            else:
                stmt = stmt.join(target, isouter=is_outer)

        # Apply a criterion (expressions)
        if self._criterion:
            stmt = stmt.where(and_(*self._criterion))

        # Apply keyword filters
        if self._kw_filters:
            conditions = LookupCompiler.compile_filters(self._manager.model, self._kw_filters)
            if conditions:
                stmt = stmt.where(and_(*conditions))

        # Grouping and aggregate filtering
        if self._group_by_clauses:
            stmt = stmt.group_by(*self._group_by_clauses)

        if self._havings:
            stmt = stmt.having(and_(*self._havings))

        # Apply eager loading (joinedload). Only valid for whole-model rows:
        # an explicit `values()` list is not an entity result set.
        if self._eager_loads and self._select_columns is None:
            for rel in self._eager_loads:
                # Spelling and typo handling live in one place: a name the model
                # does not have raises there instead of being skipped here.
                eager_option = self._manager.build_eager_option(rel)
                if eager_option is not None:
                    stmt = stmt.options(eager_option)

        # Apply prefetch loading (selectinload) — whole-model rows only, for the
        # same reason as joinedload above.
        if self._prefetch_loads and self._select_columns is None:
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

