"""Compile the existing manager lookup DSL without owning sessions or transactions."""

from typing import Any, Type, cast

from sqlalchemy import ColumnElement


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

