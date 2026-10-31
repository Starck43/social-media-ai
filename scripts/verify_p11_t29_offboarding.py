#!/usr/bin/env python3
"""Run the serialized local P11-T29 database and Safari acceptance path.

This runner is deliberately opt-in (`--execute`). It only targets the owner's
loopback ``social_manager/test_schema`` and holds advisory lock 1370093 across
preflight, migration, tests, cleanup, downgrade, and final inventory checks.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
LOCK_ID = 1370093
EXPECTED_REVISION = "baseline_0118"
TARGET_REVISION = "0119"


def resolve_target() -> tuple[str, str]:
    load_dotenv(ROOT / ".env")
    raw = os.environ.get("TEST_POSTGRES_URL", "").strip() or os.environ.get("POSTGRES_URL", "").strip()
    schema = os.environ.get("DB_TEST_SCHEMA", "test_schema").strip()
    if not raw:
        raise RuntimeError("TEST_POSTGRES_URL or POSTGRES_URL must select the local acceptance database")
    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"postgresql", "postgresql+psycopg2", "postgresql+asyncpg"}
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or (parsed.port or 5432) != 5432
        or parsed.path != "/social_manager"
        or schema != "test_schema"
    ):
        raise RuntimeError("STOP: P11-T29 acceptance requires localhost:5432/social_manager/test_schema")
    return raw, schema


def psycopg_dsn(raw: str) -> str:
    return raw.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg2://", "postgresql://")


def database_snapshot(conn, schema: str) -> dict:
    """Capture schema inventories plus exact P11-T29 reference rows/revision."""
    from psycopg2.extras import RealDictCursor

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        resources = ("audit.workspace_offboarding", "tenancy.workspace")
        cur.execute("SELECT version_num FROM test_schema.alembic_version ORDER BY version_num")
        revisions = sorted(row["version_num"] for row in cur.fetchall())
        cur.execute(
            """SELECT id, resource_key, model_name, table_name, description, is_managed, created_at, updated_at
                 FROM test_schema.model_types WHERE resource_key = ANY(%s) ORDER BY resource_key""",
            (list(resources),),
        )
        model_types = [dict(row) for row in cur.fetchall()]
        cur.execute(
            """SELECT p.id, mt.resource_key, p.name, p.action_type::text AS action_type
                 FROM test_schema.permissions p JOIN test_schema.model_types mt ON mt.id = p.model_type_id
                WHERE mt.resource_key = ANY(%s) ORDER BY mt.resource_key, p.action_type::text""",
            (list(resources),),
        )
        permissions = [dict(row) for row in cur.fetchall()]
        cur.execute(
            """SELECT r.codename AS role, p.id AS permission_id, p.action_type::text AS action_type
                 FROM test_schema.role_permission rp
                 JOIN test_schema.roles r ON r.id = rp.role_id
                 JOIN test_schema.permissions p ON p.id = rp.permission_id
                 JOIN test_schema.model_types mt ON mt.id = p.model_type_id
                WHERE mt.resource_key = ANY(%s) ORDER BY mt.resource_key, r.codename, p.action_type::text""",
            (list(resources),),
        )
        role_permissions = [dict(row) for row in cur.fetchall()]
        cur.execute(
            """SELECT r.codename AS role, p.id AS permission_id, p.action_type::text AS action_type
                 FROM test_schema.workspace_role_permission rp
                 JOIN test_schema.workspace_roles r ON r.id = rp.role_id
                 JOIN test_schema.permissions p ON p.id = rp.permission_id
                 JOIN test_schema.model_types mt ON mt.id = p.model_type_id
                WHERE mt.resource_key = ANY(%s) ORDER BY mt.resource_key, r.codename, p.action_type::text""",
            (list(resources),),
        )
        workspace_role_permissions = [dict(row) for row in cur.fetchall()]
        cur.execute(
            """SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s AND c.relkind IN ('r','p','S') ORDER BY c.relname""",
            (schema,),
        )
        relations = [row["relname"] for row in cur.fetchall()]
        (
            cur.execute("""SELECT count(*) AS row_count FROM test_schema.workspace_offboarding_events""")
            if "workspace_offboarding_events" in relations
            else None
        )
        event_count = cur.fetchone()["row_count"] if "workspace_offboarding_events" in relations else 0
        return {
            "revision": revisions,
            "model_types": model_types,
            "permissions": permissions,
            "role_permissions": role_permissions,
            "workspace_role_permissions": workspace_role_permissions,
            "relations": relations,
            "offboarding_event_count": event_count,
        }


def inventory(schema: str, env: dict[str, str]) -> dict:
    """Use the repository inventory script so every schema object is compared."""
    # The existing script opens its own connection from env; schema snapshots
    # are also captured by the held connection for revision/catalog safety.
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/ci/schema_inventory.py"), schema],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def assert_no_other_sessions(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""SELECT backend_type, coalesce(state, 'unknown'), count(*)
                 FROM pg_stat_activity
                WHERE datname = current_database() AND pid <> pg_backend_pid()
                  AND backend_type = 'client backend'
                GROUP BY backend_type, coalesce(state, 'unknown')
                ORDER BY backend_type, coalesce(state, 'unknown')""")
        sessions = cur.fetchall()
    if sessions:
        raise RuntimeError(f"STOP: other DB client sessions are active (backend type/state/count): {sessions}")


def run(command: list[str], env: dict[str, str]) -> None:
    print("RUN:", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="perform migration, tests, downgrade, and cleanup")
    parser.add_argument("--plan", action="store_true", help="validate and print the target without connecting")
    args = parser.parse_args()
    raw, schema = resolve_target()
    print(f"Target: localhost:5432/social_manager/{schema}; migration {EXPECTED_REVISION} -> {TARGET_REVISION}")
    print("Sequence: inventory public+test_schema; verify baseline; upgrade; focused pytest; Safari via live test;")
    print("          fixture cleanup; explicit test-only downgrade; inventory and exact catalog/reference comparison.")
    if args.plan or not args.execute:
        print("No database connection made. Pass --execute only during the approved exclusive test_schema window.")
        return 0

    import psycopg2

    dsn = psycopg_dsn(raw)
    env = os.environ.copy()
    env["TEST_POSTGRES_URL"] = raw
    env["DB_TEST_SCHEMA"] = schema
    # Root conftest redirects POSTGRES_URL before imports; explicitly align both
    # names to the already-validated local disposable database.
    env["POSTGRES_URL"] = raw
    env["DB_SCHEMA"] = "public"
    inventory_env = env.copy()
    inventory_env["POSTGRES_URL"] = psycopg_dsn(raw)
    env["PYTHONPATH"] = "." + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    # The P11-T28 catalog row is required by root-conftest reference checks.
    # Its helper takes/releases this same advisory lock before our snapshots.
    run([sys.executable, "scripts/prepare_p11_t28_test_schema.py"], env)
    with psycopg2.connect(dsn) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,))
            if not cur.fetchone()[0]:
                raise RuntimeError(f"STOP: shared test-schema lock {LOCK_ID} is already held")
        assert_no_other_sessions(conn)
        try:
            public_before = inventory("public", inventory_env)
            test_before_layout = inventory(schema, inventory_env)
            before = database_snapshot(conn, schema)
            if before["revision"] != [EXPECTED_REVISION]:
                raise RuntimeError(f"STOP: expected {EXPECTED_REVISION}, found {before['revision']}")
            failure = None
            try:
                migration_env = env.copy()
                migration_env["DB_SCHEMA"] = schema
                migration_env["POSTGRES_URL"] = psycopg_dsn(raw)
                run([sys.executable, "-m", "alembic", "upgrade", TARGET_REVISION], migration_env)
                run(
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        "-q",
                        "tests/admin/test_p11_t29_offboarding.py",
                        "tests/admin/test_p11_t29_journal_guards.py",
                        "tests/admin/test_p11_t29_offboarding_postgres.py",
                        "tests/admin/test_p11_t28_live_workspace_console.py",
                        "tests/security/test_access_policy.py",
                        "tests/tooling/test_p11_t24_schema_guards.py",
                    ],
                    env,
                )
            except BaseException as exc:
                failure = exc
            finally:
                with conn.cursor() as cur:
                    cur.execute("SELECT version_num FROM test_schema.alembic_version ORDER BY version_num")
                    revision_now = [row[0] for row in cur.fetchall()]
                if revision_now == [TARGET_REVISION]:
                    run(
                        [
                            sys.executable,
                            "-m",
                            "alembic",
                            "-x",
                            "p11_t29_test_teardown=1",
                            "downgrade",
                            EXPECTED_REVISION,
                        ],
                        migration_env,
                    )
                elif revision_now != [EXPECTED_REVISION]:
                    print(f"CLEANUP REQUIRED: migration ended at {revision_now}; did not issue unsafe downgrade")
                if failure is not None:
                    print("Acceptance failed; test-schema state was downgraded when revision permitted.")

            after = database_snapshot(conn, schema)
            test_after_layout = inventory(schema, inventory_env)
            public_after = inventory("public", inventory_env)
            if test_after_layout != test_before_layout:
                raise RuntimeError("test_schema layout differs after downgrade")
            if public_after != public_before:
                raise RuntimeError("public schema layout changed during P11-T29 validation")
            if after != before:
                raise RuntimeError(
                    f"test_schema revision/reference state differs after cleanup: before={before}; after={after}"
                )
            print(
                "PASS: test_schema restored to baseline; public layout unchanged; journal and reference rows match pre-run state."
            )
            if failure is not None:
                raise failure
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
