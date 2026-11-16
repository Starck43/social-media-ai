"""Relation-path resolution and loader options; no database/session ownership."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, Sequence, Type, cast

from sqlalchemy import ColumnElement
from sqlalchemy.orm import InstrumentedAttribute, QueryableAttribute, joinedload, selectinload

from .lookup_compiler import LookupCompiler

if TYPE_CHECKING:
    from .base_manager import QuerySet


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


def relation_path_parts(relation: str) -> list[str]:
    """Split a relation path into its segments, accepting both spellings.

    `select_related` nested with Django's `"source__platform"` while
    `prefetch_related` nested with SQLAlchemy's `"role.permissions"`, and each
    silently ignored the other's spelling: the eager load simply did not happen
    and the caller met a `DetachedInstanceError` much later, far from the typo.
    Both spellings now mean the same thing in both methods, and a path that does
    not resolve raises at the call site instead of loading nothing.
    """
    return [part for part in relation.replace(".", "__").split("__") if part]


class RelationLoadingMixin:
    """Loader-option behavior shared by the stable BaseManager facade."""

    @staticmethod
    def resolve_relation_chain(model: Type[Any], path: str) -> list[QueryableAttribute[Any]] | None:
        """The relationship attributes a relation path names, or None.

        None means "this path is not a chain of relationships on this model" —
        a typo, or a column where a relationship is expected. Callers raise with
        the offending name; they must not skip the load quietly.
        """
        chain: list[QueryableAttribute[Any]] = []
        current: Type[Any] = model
        for part in relation_path_parts(path):
            attr = getattr(current, part, None)
            mapper = getattr(getattr(attr, "property", None), "mapper", None)
            if attr is None or mapper is None:
                return None
            chain.append(cast(QueryableAttribute[Any], attr))
            current = mapper.class_
        return chain or None

    @staticmethod
    def _unknown_relation(relation: str, method: str) -> str:
        return (
            f"{method}({relation!r}): no such relation on this model. Nested paths "
            f"may be written 'a.b' or 'a__b'; an unknown name raises instead of "
            f"quietly loading nothing."
        )

    def build_prefetch_option(self, relation: str | QueryableAttribute[Any] | InstrumentedAttribute[Any] | Prefetch):
        """Create the selectinload option for a relation path or Prefetch descriptor."""
        if isinstance(relation, Prefetch):
            return self.build_prefetch_from_descriptor(relation)

        if isinstance(relation, (QueryableAttribute, InstrumentedAttribute)):
            return selectinload(relation)

        if isinstance(relation, str):
            chain = self.resolve_relation_chain(self.model, relation)
            if chain is None:
                raise ValueError(self._unknown_relation(relation, "prefetch_related"))
            option = selectinload(chain[0])
            for attr in chain[1:]:
                option = option.selectinload(attr)
            return option

        return None

    def build_eager_option(self, relation: str | QueryableAttribute[Any] | InstrumentedAttribute[Any]):
        """Create the joinedload option for a relation path.

        The joinedload twin of `build_prefetch_option`, with the same spelling
        rules: `"source__platform"` and `"source.platform"` both resolve, and a
        name the model does not have raises instead of being skipped.
        """
        if isinstance(relation, (QueryableAttribute, InstrumentedAttribute)):
            return joinedload(relation)

        if isinstance(relation, str):
            chain = self.resolve_relation_chain(self.model, relation)
            if chain is None:
                raise ValueError(self._unknown_relation(relation, "select_related"))
            option = joinedload(chain[0])
            for attr in chain[1:]:
                option = option.joinedload(attr)
            return option

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
            if len(relation_path_parts(path)) > 1:
                if filters or criteria or queryset:
                    raise ValueError("Filtered prefetch is not supported for nested relation paths")
                chain = self.resolve_relation_chain(self.model, path)
                if chain is None:
                    raise ValueError(self._unknown_relation(path, "prefetch_related"))
                option = selectinload(chain[0])
                for nested in chain[1:]:
                    option = option.selectinload(nested)
                return option
            chain = self.resolve_relation_chain(self.model, path)
            if chain is None:
                # Was a silent `return None`: a typo'd name loaded nothing and
                # the failure surfaced later as a DetachedInstanceError.
                raise ValueError(self._unknown_relation(path, "prefetch_related"))
            attr = chain[0]
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
