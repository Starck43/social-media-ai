"""Value formatters shared by all sqladmin views.

sqladmin resolves `column_type_formatters` by the *Python type of the value*, so
registering the date/datetime/None formatters once on `BaseAdmin` renders every
Date and DateTime column of every admin view (list, detail and CSV export) in
one Russian day-first format. Per-column `column_formatters` lambdas are only
needed when a view wants something genuinely different from the defaults.

Registered types are matched first by exact type, then by direct base class, so
`datetime.datetime` (which subclasses `datetime.date`) keeps the timestamp
format while plain `date` columns render without a time part.
"""

import datetime
from decimal import Decimal
from typing import Any, Optional, Union

from markupsafe import Markup
from sqladmin.formatters import bool_formatter

# Rendered instead of a blank cell or the literal "None" for missing values.
EMPTY = "—"

#: Value types accepted by the helpers below.
DateLike = Union[datetime.datetime, datetime.date, None]

__all__ = [
    "EMPTY",
    "bool_formatter",
    "date_formatter",
    "datetime_formatter",
    "empty_formatter",
    "format_date",
    "format_datetime",
    "format_usd",
]


def empty_formatter(value: Any) -> Markup:
    """Show a dash for NULL instead of an empty cell."""

    return Markup(EMPTY)


def datetime_formatter(value: datetime.datetime) -> str:
    """Timestamp as `DD.MM.YYYY HH:MM` — seconds are noise in an admin table."""

    return value.strftime("%d.%m.%Y %H:%M")


def date_formatter(value: datetime.date) -> str:
    """Date as `DD.MM.YYYY`."""

    return value.strftime("%d.%m.%Y")


def format_datetime(value: Optional[datetime.datetime]) -> str:
    """Per-column formatter helper for a nullable timestamp."""

    return datetime_formatter(value) if value else EMPTY


def format_date(value: DateLike) -> str:
    """Per-column formatter helper for a nullable date (drops any time part).

    Used for `DateTime` columns that are semantically dates — `Source.date_from`
    and `Source.date_to` are collected through a date-only field, so showing
    `00:00` next to them would only add clutter.
    """

    return date_formatter(value) if value else EMPTY


def format_usd(cents: Optional[Union[int, float, Decimal]]) -> str:
    """Render a cost stored in USD cents as `$0.0000`.

    Accepts the `Decimal` that `AIAnalytics.estimated_cost` returns since
    migration 0060 (NUMERIC(14,6)) as well as plain ints/floats. Matches the
    money block of the analytics detail template so the list and the card never
    disagree about the unit.
    """

    if cents is None:
        return EMPTY
    return f"${cents / 100:.4f}"
