"""Bootstrap compatibility with real metadata and catalog/engine doubles.

No application, database, conftest, schema operations or providers.
Run with the project .venv directly, or through the approved pytest runner with
--noconftest. These checks do not certify a live PostgreSQL catalog or isolation.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy import Column, Integer, MetaData, Table, UniqueConstraint

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import setup_test_db as bootstrap  # noqa: E402

SCHEMA = "review_target"


def metadata(*, unique=True):
    value = MetaData()
    constraints = [UniqueConstraint("tenant_id", "name", name="uq_items")] if unique else []
    Table(
        "items",
        value,
        Column("id", Integer, primary_key=True),
        Column("tenant_id", Integer),
        Column("name", Integer),
        *constraints,
        schema=SCHEMA,
    )
    return value


class Catalog:
    """Return scoped catalog rows; reject any attempted DDL/DML."""

    def __init__(self, *, tables=None, columns=None, uniques=None, events=None):
        self.tables = {"items"} if tables is None else set(tables)
        self.columns = (
            [("items", "id"), ("items", "tenant_id"), ("items", "name")] if columns is None else list(columns)
        )
        self.uniques = [("items", "uq_items", "name,tenant_id")] if uniques is None else list(uniques)
        self.events = [] if events is None else events
        self.calls = []

    async def execute(self, statement, params=None):
        sql = str(statement).lower()
        self.calls.append((sql, params))
        self.events.append("read")
        if sql == "set transaction isolation level repeatable read, read only":
            return []
        if not sql.startswith("select "):
            raise AssertionError(f"non-read statement: {sql}")
        if params != {"schema": SCHEMA}:
            raise AssertionError("catalog lookup must bind the exact target schema")
        if "information_schema.tables" in sql:
            return [(name,) for name in sorted(self.tables)]
        if "information_schema.columns" in sql:
            return self.columns
        if "information_schema.table_constraints" in sql:
            if "kcu.table_name = tc.table_name" not in sql:
                raise AssertionError("same-named constraints from other tables must not join")
            return self.uniques
        raise AssertionError(f"unexpected query: {sql}")


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_matching_existing_catalog_is_read_only(self):
        conn = Catalog()
        await bootstrap.check_existing_schema(conn, metadata(), SCHEMA)
        self.assertEqual(len(conn.calls), 3)
        self.assertTrue(all(sql.startswith("select ") for sql, _ in conn.calls))

    async def test_outcome_table_missing_from_branch_stops_before_other_reads(self):
        conn = Catalog(
            tables={"items", "llm_outcome_events"},
            uniques=[
                ("items", "uq_items", "name,tenant_id"),
                ("llm_outcome_events", "uq_llm_outcome_events_identity", "event_id"),
            ],
        )
        with self.assertRaisesRegex(RuntimeError, "absent from this branch.*llm_outcome_events"):
            await bootstrap.check_existing_schema(conn, metadata(), SCHEMA)
        self.assertEqual(len(conn.calls), 1)

    async def test_unknown_table_without_unique_also_stops(self):
        conn = Catalog(tables={"items", "future_evidence"}, uniques=[])
        with self.assertRaisesRegex(RuntimeError, "future_evidence"):
            await bootstrap.check_existing_schema(conn, metadata(), SCHEMA)

    async def test_own_table_without_uniques_is_not_unknown(self):
        await bootstrap.check_existing_schema(Catalog(uniques=[]), metadata(unique=False), SCHEMA)

    async def test_missing_column_stops_before_unique_read(self):
        conn = Catalog(columns=[("items", "id"), ("items", "tenant_id")])
        with self.assertRaisesRegex(RuntimeError, "missing model columns: items.name"):
            await bootstrap.check_existing_schema(conn, metadata(), SCHEMA)
        self.assertEqual(len(conn.calls), 2)

    async def test_stale_unique_is_reported_never_dropped(self):
        conn = Catalog(uniques=[("items", "uq_items", "name,tenant_id"), ("items", "old_key", "name")])
        with self.assertRaisesRegex(RuntimeError, "unexpected items.old_key"):
            await bootstrap.check_existing_schema(conn, metadata(), SCHEMA)
        self.assertEqual(len(conn.calls), 3)

    async def test_missing_unique_is_reported_never_added(self):
        with self.assertRaisesRegex(RuntimeError, "missing items.uq_items"):
            await bootstrap.check_existing_schema(Catalog(uniques=[]), metadata(), SCHEMA)

    async def test_same_named_wrong_column_unique_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "unexpected items.uq_items.*missing items.uq_items"):
            await bootstrap.check_existing_schema(Catalog(uniques=[("items", "uq_items", "name")]), metadata(), SCHEMA)

    async def test_database_auto_name_for_same_columns_is_compatible(self):
        await bootstrap.check_existing_schema(
            Catalog(uniques=[("items", "items_tenant_name_key", "name,tenant_id")]), metadata(), SCHEMA
        )

    async def test_missing_model_table_can_use_fresh_table_path(self):
        await bootstrap.check_existing_schema(Catalog(tables=[], columns=[], uniques=[]), metadata(), SCHEMA)

    async def test_post_creation_check_requires_all_model_tables(self):
        with self.assertRaisesRegex(RuntimeError, "model tables still absent.*items"):
            await bootstrap.check_existing_schema(
                Catalog(tables=[], columns=[], uniques=[]), metadata(), SCHEMA, require_all_tables=True
            )

    async def test_alembic_version_table_is_not_orm_drift(self):
        await bootstrap.check_existing_schema(Catalog(tables={"items", "alembic_version"}), metadata(), SCHEMA)

    async def test_other_schema_metadata_cannot_authorize_a_live_table(self):
        value = metadata()
        Table("future_evidence", value, Column("id", Integer), schema="another_target")
        with self.assertRaisesRegex(RuntimeError, "future_evidence"):
            await bootstrap.check_existing_schema(Catalog(tables={"items", "future_evidence"}), value, SCHEMA)

    async def test_catalog_error_is_not_suppressed(self):
        conn = SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError("catalog unavailable")))
        with self.assertRaisesRegex(RuntimeError, "catalog unavailable"):
            await bootstrap.check_existing_schema(conn, metadata(), SCHEMA)


class ConnectionContext:
    def __init__(self, engine):
        self.engine = engine

    async def __aenter__(self):
        self.engine.events.append("connect")
        return self.engine.catalog

    async def __aexit__(self, *args):
        self.engine.events.append("close")
        return False


class Engine:
    def __init__(self, catalog):
        self.catalog = catalog
        self.events = catalog.events
        self.dispose = AsyncMock()

    def connect(self):
        return ConnectionContext(self)


def module(name, **attributes):
    value = ModuleType(name)
    value.__dict__.update(attributes)
    return value


class BootstrapOrderTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, conn, *, reset=False, state="exists", shares=True, create_side_effect=None):
        engine = Engine(conn)
        mocks = {
            "_create_database_if_missing": AsyncMock(return_value=state),
            "_table_count": AsyncMock(return_value=len(conn.tables)),
            "_create_schema": AsyncMock(side_effect=lambda: conn.events.append("create_schema")),
            "_create_tables": AsyncMock(
                side_effect=create_side_effect or (lambda: conn.events.append("create_tables"))
            ),
            "_truncate_all": AsyncMock(side_effect=lambda: conn.events.append("truncate")),
            "_stamp_head": Mock(side_effect=lambda url: conn.events.append("stamp")),
            "_seed_reference_rows": AsyncMock(side_effect=lambda: conn.events.append("seed")),
        }
        modules = {
            "app": module("app", __path__=[]),
            "app.core": module("app.core", __path__=[]),
            "app.core.database": module("app.core.database", async_engine=engine),
            "app.core.config": module("app.core.config", settings=SimpleNamespace(DB_SCHEMA=SCHEMA)),
            "app.models": module("app.models", Base=SimpleNamespace(metadata=metadata())),
        }
        with patch.dict(sys.modules, modules), patch.multiple(bootstrap, **mocks):
            try:
                await bootstrap.ensure_test_database(
                    "postgresql+asyncpg://example/review_db", shares_working_database=shares, reset=reset
                )
            except Exception as error:
                return mocks, engine, error
        return mocks, engine, None

    def assert_no_target_writes(self, mocks, engine):
        for name in ("_create_schema", "_create_tables", "_truncate_all", "_seed_reference_rows"):
            mocks[name].assert_not_awaited()
        mocks["_stamp_head"].assert_not_called()
        self.assertNotIn("seed", engine.events)

    async def test_unknown_outcome_stops_before_create_or_seed(self):
        mocks, engine, error = await self.invoke(Catalog(tables={"items", "llm_outcome_events"}))
        self.assertIsInstance(error, RuntimeError)
        self.assert_no_target_writes(mocks, engine)
        self.assertEqual(engine.catalog.calls[0][0], "set transaction isolation level repeatable read, read only")

    async def test_reset_cannot_bypass_unknown_table_guard(self):
        mocks, engine, error = await self.invoke(Catalog(tables={"items", "llm_outcome_events"}), reset=True)
        self.assertIsInstance(error, RuntimeError)
        self.assert_no_target_writes(mocks, engine)

    async def test_known_unique_drift_stops_before_create_or_seed(self):
        mocks, engine, error = await self.invoke(Catalog(uniques=[("items", "old_key", "name")]))
        self.assertIsInstance(error, RuntimeError)
        self.assert_no_target_writes(mocks, engine)

    async def test_missing_columns_stop_before_reset_or_seed(self):
        mocks, engine, error = await self.invoke(Catalog(columns=[("items", "id")]), reset=True)
        self.assertIsInstance(error, RuntimeError)
        self.assert_no_target_writes(mocks, engine)

    async def test_existing_compatible_schema_keeps_seed_path_after_both_checks(self):
        mocks, engine, error = await self.invoke(Catalog())
        self.assertIsNone(error)
        mocks["_create_tables"].assert_awaited_once()
        mocks["_seed_reference_rows"].assert_awaited_once()
        mocks["_truncate_all"].assert_not_awaited()
        mocks["_stamp_head"].assert_not_called()
        self.assertEqual(engine.events.count("connect"), 2)
        self.assertLess(engine.events.index("close"), engine.events.index("create_schema"))
        self.assertLess(
            max(i for i, event in enumerate(engine.events) if event == "close"), engine.events.index("seed")
        )

    async def test_empty_shared_target_keeps_fresh_table_path_without_stamp(self):
        conn = Catalog(tables=[], columns=[], uniques=[])

        def create():
            conn.events.append("create_tables")
            conn.tables = {"items"}
            conn.columns = [("items", "id"), ("items", "tenant_id"), ("items", "name")]
            conn.uniques = [("items", "uq_items", "name,tenant_id")]

        mocks, engine, error = await self.invoke(conn, create_side_effect=create)
        self.assertIsNone(error)
        mocks["_create_tables"].assert_awaited()
        mocks["_seed_reference_rows"].assert_awaited_once()
        mocks["_stamp_head"].assert_not_called()

    async def test_post_creation_drift_stops_seeding(self):
        conn = Catalog()

        def changed_catalog():
            conn.events.append("create_tables")
            conn.uniques = []

        mocks, engine, error = await self.invoke(conn, create_side_effect=changed_catalog)
        self.assertIsInstance(error, RuntimeError)
        mocks["_seed_reference_rows"].assert_not_awaited()

    async def test_post_creation_missing_tables_stop_seeding(self):
        mocks, engine, error = await self.invoke(Catalog(tables=[], columns=[], uniques=[]))
        self.assertIsInstance(error, RuntimeError)
        self.assertIn("model tables still absent", str(error))
        mocks["_seed_reference_rows"].assert_not_awaited()

    async def test_created_independent_target_retains_version_stamp_path(self):
        conn = Catalog(tables=[], columns=[], uniques=[])

        def create():
            conn.tables = {"items"}
            conn.columns = [("items", "id"), ("items", "tenant_id"), ("items", "name")]
            conn.uniques = [("items", "uq_items", "name,tenant_id")]

        mocks, engine, error = await self.invoke(conn, state="created", shares=False, create_side_effect=create)
        self.assertIsNone(error)
        mocks["_stamp_head"].assert_called_once_with("postgresql+asyncpg://example/review_db")
        mocks["_seed_reference_rows"].assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
