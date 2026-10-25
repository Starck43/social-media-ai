"""Tests for the test-database bootstrap (``scripts.setup_test_db``).

The drift check compares the model metadata against what actually exists in the
schema, because ``create_all(checkfirst=True)`` adds missing *tables* but never
missing *columns* — a schema that predates a model change silently breaks every
test that writes the new column with a bare ``column "x" does not exist``.
"""

from __future__ import annotations

import secrets

from sqlalchemy import Column, Integer, MetaData, Table, Text, text

from app.core.database import async_engine
from scripts.setup_test_db import _missing_columns


def _schema_name() -> str:
    return f"drift_check_{secrets.token_hex(3)}"


async def test_missing_columns_reports_dropped_but_not_present():
    schema = _schema_name()
    metadata = MetaData()
    Table("items", metadata, Column("id", Integer, primary_key=True), Column("name", Text), schema=schema)

    async with async_engine.connect() as conn:
        trans = await conn.begin()
        try:
            await conn.execute(text(f'create schema "{schema}"'))
            await conn.execute(text(f'create table "{schema}"."items" (id integer primary key, name text)'))

            # A schema that matches the metadata reports nothing missing...
            assert await _missing_columns(conn, metadata, schema) == []

            # ...and a column the model has but the schema lost is named exactly.
            await conn.execute(text(f'alter table "{schema}"."items" drop column name'))
            assert await _missing_columns(conn, metadata, schema) == ["items.name"]
        finally:
            await trans.rollback()
