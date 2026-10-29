"""Rebuild the explicitly disposable local test_schema using the initial revision.

Never accepts a public target. Public is fingerprinted read-only before/after.
Run with --reset-test-schema only after coordinating exclusive local DB ownership.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
LOCK = 1370014


def public_fingerprint(connection):
    """Catalog definitions and row counts; never return application row contents."""
    inspector = inspect(connection)
    tables = inspector.get_table_names(schema="public")
    preparer = connection.dialect.identifier_preparer
    counts = {
        table: connection.execute(text(f"SELECT count(*) FROM public.{preparer.quote(table)}")).scalar_one()
        for table in tables
    }
    definitions = (
        connection.execute(
            text(
                "SELECT pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='public' AND p.prokind IN ('f','p') ORDER BY p.oid"
            )
        )
        .scalars()
        .all()
    )
    revision = connection.execute(text("SELECT version_num FROM public.alembic_version")).scalars().all()
    return {"counts": counts, "functions": definitions, "revision": revision}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset-test-schema", action="store_true", required=True)
    args = parser.parse_args()
    from app.core.config import settings
    from app.core.database import engine

    url = make_url(settings.POSTGRES_URL)
    if (
        settings.DB_SCHEMA != "test_schema"
        or settings.DB_TEST_SCHEMA != "test_schema"
        or url.database != "social_manager"
        or url.host not in (None, "", "localhost", "127.0.0.1", "/tmp")
        or url.query.get("host") not in (None, "", "localhost", "127.0.0.1", "/tmp")
    ):
        raise SystemExit("Refusing reset: expected local social_manager / test_schema")
    if not args.reset_test_schema:
        raise SystemExit("Explicit disposable-schema reset required")
    config = Config(str(ROOT / "alembic.ini"))
    with engine.connect() as lock:
        database, address = lock.execute(text("SELECT current_database(), inet_server_addr()::text")).one()
        if database != "social_manager" or address not in (None, "127.0.0.1", "::1"):
            raise SystemExit("Resolved database endpoint is not the authorized local target")
        if not lock.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK}).scalar_one():
            raise SystemExit("Another Q-14 validator owns the database")
        try:
            sessions = (
                lock.execute(
                    text(
                        "SELECT pid FROM pg_stat_activity WHERE datname=current_database() "
                        "AND pid<>pg_backend_pid() AND backend_type='client backend'"
                    )
                )
                .scalars()
                .all()
            )
            if sessions:
                raise SystemExit(f"Coordinate other database sessions before reset: {sessions}")
            before = public_fingerprint(lock)
            lock.commit()
            with engine.begin() as connection:
                operations = Operations(MigrationContext.configure(connection))
                operations.execute('DROP SCHEMA IF EXISTS "test_schema" CASCADE')
                operations.execute('CREATE SCHEMA "test_schema"')
            command.upgrade(config, "head")

            async def prepare():
                from app.core.database import async_engine
                from app.models import Base
                from scripts.setup_test_db import _seed_reference_rows, check_existing_schema

                async with async_engine.connect() as connection:
                    await check_existing_schema(connection, Base.metadata, "test_schema", require_all_tables=True)
                await _seed_reference_rows()
                await async_engine.dispose()

            asyncio.run(prepare())
            after = public_fingerprint(lock)
            if before != after:
                raise SystemExit("Public fingerprint changed during validation; investigate before proceeding")
            revision = lock.execute(text("SELECT version_num FROM test_schema.alembic_version")).scalar_one()
            print(json.dumps({"test_schema_revision": revision, "public_unchanged": True, "reference_seed": "PASS"}))
        finally:
            lock.rollback()
            lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK})
            lock.commit()
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
