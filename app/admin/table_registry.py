"""Owner-only, read-only registry of every table the platform knows.

The owner asked for one place that shows all data tables. The constraint is
just as firm: no query box, no DDL, no DML, no export. So the registry is a
`BaseView` — it has no model, no form and no write route — and it names its
columns from the SQLAlchemy metadata instead of the ORM, which also shows
association tables and tables that never got an admin view.

Two sources, in priority order:

1. `Base.metadata` — every mapped table and association table with its columns.
2. `access_catalog.RESOURCES` — the stable resource key a table is granted
   under, so the owner can read the data *and* the permission name next to it.

Nothing here reads rows: the detail page shows the declaration of a table.
"""

import weakref

from sqlalchemy import Table

from app.core.access_catalog import _BY_TABLE

# Columns that hold material worth masking even in a schema listing; the
# registry only lists names, but the page labels the risky ones so the owner
# is not surprised by what the table carries.
SENSITIVE_COLUMN_HINTS = ("password", "token", "secret", "key", "hash")

_REGISTRY_CACHE: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _table_columns(table: Table) -> list[dict]:
    return [
        {
            "name": column.name,
            "type": str(column.type),
            "nullable": bool(column.nullable),
            "primary_key": bool(column.primary_key),
            "foreign_key": ", ".join(sorted({fk.target_fullname for fk in column.foreign_keys})) or None,
            "sensitive": any(hint in column.name.lower() for hint in SENSITIVE_COLUMN_HINTS),
        }
        for column in table.columns
    ]


def _metadata_signature(metadata) -> tuple:
    return tuple(
        sorted((table.name, len(table.columns), len(table.foreign_keys)) for table in metadata.tables.values())
    )


def build_table_registry(metadata) -> list[dict]:
    """Describe every model and association table, sorted by table name."""
    signature = _metadata_signature(metadata)
    try:
        cached = _REGISTRY_CACHE.get(metadata)
    except TypeError:
        cached = None
    if cached is not None and cached[0] == signature:
        return cached[1]

    tables = []
    for table in metadata.tables.values():
        resource = _BY_TABLE.get(table.name)
        tables.append(
            {
                "table": table.name,
                "schema": table.schema,
                "model": getattr(resource, "model_name", None),
                "resource_key": getattr(resource, "key", None),
                "columns": _table_columns(table),
                "column_names": [column.name for column in table.columns],
                "column_count": len(table.columns),
                "foreign_keys": sorted({fk.column.table.name for fk in table.foreign_keys}),
                "has_primary_key": bool(table.primary_key.columns),
            }
        )
    result = sorted(tables, key=lambda item: item["table"])
    try:
        _REGISTRY_CACHE[metadata] = (signature, result)
    except TypeError:
        pass
    return result


def filter_tables(tables: list[dict], query: str) -> list[dict]:
    """Case-insensitive match on table, model and resource key; blank matches all."""
    term = (query or "").strip().lower()
    if not term:
        return tables
    return [
        item
        for item in tables
        if term in item["table"].lower()
        or term in (item["model"] or "").lower()
        or term in (item["resource_key"] or "").lower()
    ]


def paginate_tables(tables: list[dict], page: int, page_size: int) -> dict:
    """Clamp the page the way the SQLAdmin list pages do."""
    total = len(tables)
    pages = max(1, -(-total // page_size))
    current = min(max(1, page), pages)
    start = (current - 1) * page_size
    return {
        "items": tables[start : start + page_size],
        "page": current,
        "pages": pages,
        "page_size": page_size,
        "total": total,
    }


def find_table(tables: list[dict], name: str) -> dict | None:
    return next((item for item in tables if item["table"] == name), None)
