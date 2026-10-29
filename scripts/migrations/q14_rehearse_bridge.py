"""Rehearse the public bridge on a disposable schema-only public clone.

Reads public DDL and its actual revision marker, never public application rows.
All DDL and bridge writes target test_schema through Alembic operations.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import make_url

from scripts.migrations.q14_public_bridge import apply_public_transition
from scripts.migrations.q14_validate_baseline import LOCK, ROOT, public_fingerprint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset-test-schema", action="store_true", required=True)
    parser.parse_args()
    from app.core.config import settings
    from app.core.database import engine
    from scripts.migrations.q14_compare_schemas import _catalog

    url = make_url(settings.POSTGRES_URL)
    if settings.DB_SCHEMA != "test_schema" or settings.DB_TEST_SCHEMA != "test_schema":
        raise SystemExit("Only the explicitly disposable test_schema is allowed")
    with engine.connect() as connection:
        database, address, user = connection.execute(
            text("SELECT current_database(), inet_server_addr()::text, current_user")
        ).one()
        if database != "social_manager" or address not in (None, "127.0.0.1", "::1"):
            raise SystemExit("Expected the authorized local social_manager database")
        if not connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK}).scalar_one():
            raise SystemExit("Another Q-14 writer owns the database")
        try:
            sessions = (
                connection.execute(
                    text(
                        "SELECT pid FROM pg_stat_activity WHERE datname=current_database() "
                        "AND pid<>pg_backend_pid() AND backend_type='client backend'"
                    )
                )
                .scalars()
                .all()
            )
            if sessions:
                raise SystemExit(f"Coordinate other database sessions before rehearsal: {sessions}")
            before = public_fingerprint(connection)
            if before["revision"] != ["0117"]:
                raise SystemExit("Rehearsal requires the actual public 0117 source schema")
            if (
                connection.execute(text("SELECT version_num FROM test_schema.alembic_version")).scalar_one()
                != "baseline_0118"
            ):
                raise SystemExit("Prepare the canonical baseline before rehearsing the public clone")
            connection.execute(text("SET LOCAL search_path TO pg_catalog"))
            expected_catalog = _catalog(connection, "test_schema")
            env = os.environ.copy()
            env.update(
                PGDATABASE=database,
                PGUSER=user,
                PGHOST="/tmp" if address is None else address,
                PGPORT=str(url.port or 5432),
            )
            if url.password is not None:
                env["PGPASSWORD"] = url.password
            dump = subprocess.check_output(
                ["pg_dump", "--schema-only", "--schema=public", "--no-owner", "--no-privileges"],
                env=env,
                text=True,
            )
            lines = [
                line
                for line in dump.splitlines()
                if not line.startswith(("--", "\\", "SET ", "SELECT pg_catalog.set_config("))
                and line != "CREATE SCHEMA public;"
                and not line.startswith("COMMENT ON SCHEMA public ")
            ]
            ddl = "\n".join(lines).replace("public.", '"test_schema".')
            if re.search(r"\bpublic\b", ddl):
                raise SystemExit("Unresolved public reference in schema clone; refusing execution")
            connection.commit()
            with connection.begin():
                operations = Operations(MigrationContext.configure(connection))
                operations.execute('DROP SCHEMA IF EXISTS "test_schema" CASCADE')
                operations.execute('CREATE SCHEMA "test_schema"')
                operations.execute(ddl)
                # Restore the actual source marker with its schema-only snapshot.
                operations.execute(text("INSERT INTO test_schema.alembic_version VALUES ('0117')"))

            async def seed():
                from app.core.database import async_engine
                from scripts.setup_test_db import _seed_reference_rows

                await _seed_reference_rows()
                await async_engine.dispose()

            asyncio.run(seed())
            # Close our idle seed connections; the checked-out lock connection stays.
            engine.dispose()
            with connection.begin():
                apply_public_transition(
                    connection,
                    Config(str(ROOT / "alembic.ini")),
                    "test_schema",
                    validation_test_schema=True,
                    expected_catalog=expected_catalog,
                )
            if public_fingerprint(connection) != before:
                raise SystemExit("Public fingerprint changed during rehearsal; investigate")
            print(
                "PASS: actual public 0117 DDL clone -> baseline_0118; existing seeded rows preserved; public unchanged"
            )
        finally:
            connection.rollback()
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK})
            connection.commit()
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
