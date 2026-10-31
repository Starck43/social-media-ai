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
from contextlib import closing
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
    """Capture the revision, full access catalog, and owned journal count."""
    from psycopg2 import sql
    from psycopg2.extras import RealDictCursor

    if schema not in {"public", "test_schema"}:
        raise ValueError("Only public and test_schema snapshots are supported")
    quoted = sql.Identifier(schema)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s AND c.relkind IN ('r','p','S') ORDER BY c.relname""",
            (schema,),
        )
        relations = [row["relname"] for row in cur.fetchall()]
        tables = set(relations)
        revisions = []
        if "alembic_version" in tables:
            cur.execute(sql.SQL("SELECT version_num FROM {}.alembic_version ORDER BY version_num").format(quoted))
            revisions = sorted(row["version_num"] for row in cur.fetchall())
        catalog_rows = {}
        for table, order in (
            ("model_types", "id"),
            ("permissions", "id"),
            ("roles", "id"),
            ("role_permission", "role_id, permission_id"),
            ("workspace_roles", "id"),
            ("workspace_role_permission", "role_id, permission_id"),
        ):
            if table not in tables:
                catalog_rows[table] = []
                continue
            query = sql.SQL("SELECT * FROM {}.{} ORDER BY {}").format(
                quoted,
                sql.Identifier(table),
                sql.SQL(", ").join(sql.Identifier(part.strip()) for part in order.split(",")),
            )
            cur.execute(query)
            catalog_rows[table] = [dict(row) for row in cur.fetchall()]
        business_counts = {}
        for table in (
            "users",
            "tenants",
            "action_audit_events",
            "llm_budget_days",
            "task_billing_payers",
        ):
            if table not in tables:
                business_counts[table] = None
                continue
            cur.execute(sql.SQL("SELECT count(*) AS row_count FROM {}.{}").format(quoted, sql.Identifier(table)))
            business_counts[table] = cur.fetchone()["row_count"]
        event_count = 0
        if "workspace_offboarding_events" in tables:
            cur.execute(sql.SQL("SELECT count(*) AS row_count FROM {}.workspace_offboarding_events").format(quoted))
            event_count = cur.fetchone()["row_count"]
        return {
            "revision": revisions,
            "catalog_rows": catalog_rows,
            "business_counts": business_counts,
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


def verify_normal_downgrade_refusal(conn, env: dict[str, str]) -> None:
    """A non-empty immutable journal must survive a normal revision downgrade."""
    with conn.cursor() as cur:
        cur.execute("SELECT version_num FROM test_schema.alembic_version ORDER BY version_num")
        before_revision = [row[0] for row in cur.fetchall()]
        cur.execute("SELECT count(*) FROM test_schema.workspace_offboarding_events")
        before_rows = cur.fetchone()[0]
    if before_revision != [TARGET_REVISION] or before_rows < 1:
        raise RuntimeError("Normal-downgrade refusal requires revision 0119 and at least one successful journal row")
    command = [sys.executable, "-m", "alembic", "downgrade", EXPECTED_REVISION]
    print("CHECK expected refusal:", " ".join(command), flush=True)
    result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    diagnostic = result.stdout + result.stderr
    if result.returncode == 0 or "Workspace offboarding evidence is retained" not in diagnostic:
        raise RuntimeError("A normal downgrade did not refuse retained workspace-offboarding evidence")
    with conn.cursor() as cur:
        cur.execute("SELECT version_num FROM test_schema.alembic_version ORDER BY version_num")
        after_revision = [row[0] for row in cur.fetchall()]
        cur.execute("SELECT count(*) FROM test_schema.workspace_offboarding_events")
        after_rows = cur.fetchone()[0]
    if after_revision != before_revision or after_rows != before_rows:
        raise RuntimeError("Refused downgrade changed the migration revision or immutable journal rows")
    print("PASS: normal downgrade refused without changing revision or retained journal rows.")


def assert_synthetic_prefixes_removed(conn) -> None:
    """Catch owned test rows that ordinary fixture teardown failed to remove."""
    checks = (
        (
            "users",
            "username LIKE 'p11t29-%' OR email LIKE 'p11t29-%' OR username LIKE 'p11t28-%' OR email LIKE 'p11t28-%'",
        ),
        ("tenants", "slug LIKE 'p11-t29-%' OR name LIKE 'P11-T29 %' OR slug LIKE 'p11-t28-%' OR name LIKE 'P11-T28 %'"),
        ("llm_budget_days", "baseline_version LIKE 'p11-t29-%'"),
    )
    with conn.cursor() as cur:
        for table, predicate in checks:
            cur.execute(f"SELECT count(*) FROM test_schema.{table} WHERE {predicate}")
            remaining = cur.fetchone()[0]
            if remaining:
                raise RuntimeError(f"Cleanup failure: {remaining} owned P11-T29 rows remain in {table}")


def catalog_diff(before: dict, after: dict) -> list[str]:
    """Describe catalog row changes without dumping unrelated snapshot data."""
    changes = []
    for table, rows_before in before["catalog_rows"].items():
        rows_after = after["catalog_rows"].get(table, [])
        key_fields = ("id",) if all("id" in row for row in rows_before + rows_after) else None
        if key_fields:
            old_by_key = {tuple(row[key] for key in key_fields): row for row in rows_before}
            new_by_key = {tuple(row[key] for key in key_fields): row for row in rows_after}
            for key in sorted(old_by_key.keys() | new_by_key.keys(), key=repr):
                old, new = old_by_key.get(key), new_by_key.get(key)
                if old is None:
                    changes.append(f"{table} key={key!r} added={new!r}")
                elif new is None:
                    changes.append(f"{table} key={key!r} removed={old!r}")
                elif old != new:
                    fields = sorted(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))
                    changes.append(
                        f"{table} key={key!r} changed="
                        + repr({name: (old.get(name), new.get(name)) for name in fields})
                    )
        elif rows_before != rows_after:
            old = {json.dumps(row, sort_keys=True, default=str) for row in rows_before}
            new = {json.dumps(row, sort_keys=True, default=str) for row in rows_after}
            changes.extend(f"{table} row removed={row}" for row in sorted(old - new))
            changes.extend(f"{table} row added={row}" for row in sorted(new - old))
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="perform migration, tests, downgrade, and cleanup")
    parser.add_argument("--plan", action="store_true", help="validate and print the target without connecting")
    parser.add_argument(
        "--snapshot-file", help="write full before/after inventories and catalog snapshots to this file"
    )
    args = parser.parse_args()
    raw, schema = resolve_target()
    print(f"Target: localhost:5432/social_manager/{schema}; migration {EXPECTED_REVISION} -> {TARGET_REVISION}")
    print(
        "Sequence: prepare T28 reference; advisory lock + active-session check; snapshot public+test_schema; verify baseline;"
    )
    print("          upgrade; focused pytest/Safari; cleanup; normal downgrade refusal; explicit test-only downgrade;")
    print("          exact public/test_schema layout and full reference-catalog comparison.")
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
    inventory_env["TEST_POSTGRES_URL"] = psycopg_dsn(raw)
    env["PYTHONPATH"] = "." + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    # The P11-T28 catalog row is required by root-conftest reference checks.
    # Its helper takes/releases this same advisory lock before our snapshots.
    run([sys.executable, "scripts/prepare_p11_t28_test_schema.py"], env)
    with closing(psycopg2.connect(dsn)) as conn:
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
            public_reference_before = database_snapshot(conn, "public")
            snapshot_path = Path(args.snapshot_file).resolve() if args.snapshot_file else None
            if snapshot_path is not None:
                snapshot_path.parent.mkdir(parents=True, exist_ok=True)
                snapshot_path.write_text(
                    json.dumps(
                        {
                            "phase": "before",
                            "public_reference_before": public_reference_before,
                            "public_layout_before": public_before,
                            "test_schema_before": before,
                            "test_schema_layout_before": test_before_layout,
                        },
                        indent=2,
                        sort_keys=True,
                        default=str,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                print(f"Pre-run snapshot evidence saved: {snapshot_path}", flush=True)
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
                assert_synthetic_prefixes_removed(conn)
                verify_normal_downgrade_refusal(conn, migration_env)
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
            public_reference_after = database_snapshot(conn, "public")
            test_after_layout = inventory(schema, inventory_env)
            public_after = inventory("public", inventory_env)
            if test_after_layout != test_before_layout:
                raise RuntimeError("test_schema layout differs after downgrade")
            if public_after != public_before:
                raise RuntimeError("public schema layout changed during P11-T29 validation")
            if public_reference_after != public_reference_before:
                raise RuntimeError("public schema references or catalog data changed during P11-T29 validation")
            if after != before:
                print("test_schema catalog/revision diff:", flush=True)
                for change in catalog_diff(before, after):
                    print("  " + change, flush=True)
                if before["revision"] != after["revision"]:
                    print(f"  revision: {before['revision']!r} -> {after['revision']!r}", flush=True)
                if before["relations"] != after["relations"]:
                    print(f"  relations: {before['relations']!r} -> {after['relations']!r}", flush=True)
                if before["business_counts"] != after["business_counts"]:
                    print(
                        f"  business_counts: {before['business_counts']!r} -> {after['business_counts']!r}", flush=True
                    )
                if before["offboarding_event_count"] != after["offboarding_event_count"]:
                    print(
                        "  offboarding_event_count: "
                        f"{before['offboarding_event_count']!r} -> {after['offboarding_event_count']!r}",
                        flush=True,
                    )
            if snapshot_path is not None:
                snapshot_path.write_text(
                    json.dumps(
                        {
                            "phase": "complete",
                            "public_reference_before": public_reference_before,
                            "public_reference_after": public_reference_after,
                            "public_layout_before": public_before,
                            "public_layout_after": public_after,
                            "test_schema_before": before,
                            "test_schema_after": after,
                            "test_schema_layout_before": test_before_layout,
                            "test_schema_layout_after": test_after_layout,
                        },
                        indent=2,
                        sort_keys=True,
                        default=str,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                print(f"Snapshot evidence: {snapshot_path}", flush=True)
            if after != before:
                raise RuntimeError("test_schema revision/reference state differs after cleanup")
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
