"""Prepare/clean one disposable P21-T11 schema; parent runner owns these steps.

Requires an explicit local database URL and DB_TEST_SCHEMA=test_p21t11_<suffix>.
No credential environment file or application row from public is inspected.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "cleanup"))
    parser.add_argument("--inventory", type=Path, required=True)
    args = parser.parse_args()
    schema = os.environ.get("DB_TEST_SCHEMA", "")
    if not re.fullmatch(r"test_p21t11_[a-z0-9_]{1,32}", schema) or not os.environ.get("POSTGRES_URL"):
        raise SystemExit("An explicit task-owned test schema and local DB URL are required")
    if Path(".env").exists():
        raise SystemExit("Run in the secret-free task worktree, not a checkout with .env")
    from scripts.setup_test_db import resolve_and_redirect

    resolve_and_redirect()
    from app.core.config import settings
    from app.core.database import async_engine, engine
    from scripts.ci.schema_inventory import snapshot

    config = Config("alembic.ini")
    config.attributes["skip_model_type_registration_on_stamp"] = True
    with engine.connect() as conn:
        target = conn.execute(text("SELECT current_database(), inet_server_addr()::text")).one()
        address = target[1].split("/")[0] if target[1] else None
        if target[0] != "social_manager" or address not in ("127.0.0.1", "::1", None) or settings.DB_SCHEMA != schema:
            raise SystemExit("Only the resolved local social_manager task schema is authorized")
        if not conn.execute(text("SELECT pg_try_advisory_lock(1370093)")).scalar_one():
            raise SystemExit("Another DB runner owns the advisory lock")
        try:
            writers = conn.execute(text("""SELECT pid FROM pg_stat_activity WHERE datname=current_database()
                AND pid<>pg_backend_pid() AND state='active'
                AND query ~* '\\m(insert|update|delete|alter|create|drop|truncate)\\M'""")).scalars().all()
            if writers:
                raise SystemExit("Coordinate active database writers before proceeding")
            exists = conn.execute(text("SELECT to_regnamespace(:schema)"), {"schema": schema}).scalar()
            conn.commit()
            if args.action == "prepare":
                if exists is not None or args.inventory.exists():
                    raise SystemExit("Refusing to overwrite an existing schema or inventory")
                args.inventory.parent.mkdir(parents=True, exist_ok=True)
                before = {name: snapshot(name) for name in ("public", "test_schema", schema)}
                args.inventory.write_text(json.dumps(before, sort_keys=True), encoding="utf-8")
                print(
                    f"Baseline dev graph predecessor: {ScriptDirectory.from_config(config).get_revision('0153').down_revision}"
                )
                print(f"Observed shared test revision: {before['test_schema'].get('alembic_version_max')}")
                with conn.begin():
                    conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                    config.attributes["connection"] = conn
                    command.upgrade(config, "head")

                async def seed():
                    from scripts.setup_test_db import _seed_reference_rows

                    await _seed_reference_rows()
                    await async_engine.dispose()

                asyncio.run(seed())
                print("Prepared task-isolated Alembic head and synthetic reference rows")
            else:
                if not args.inventory.exists():
                    raise SystemExit("Cleanup requires the inventory from the owned preparation")
                before = json.loads(args.inventory.read_text(encoding="utf-8"))
                with conn.begin():
                    if exists is not None:
                        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
                after = {name: snapshot(name) for name in ("public", "test_schema", schema)}
                if before != after:
                    raise SystemExit("Schema inventories differ after cleanup; preserve evidence and investigate")
                print("PASS: owned schema/synthetic rows absent; public and test_schema inventories unchanged")
        finally:
            conn.rollback()
            conn.execute(text("SELECT pg_advisory_unlock(1370093)"))
            conn.commit()
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
