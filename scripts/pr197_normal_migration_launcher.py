"""One-step normal Alembic maintenance for the reviewed PR197 migration target.

Prepared for the PR197 local acceptance package. This launcher:

* suppresses BOTH dotenv entry points (process environment wins over any file),
* verifies the effective target BEFORE any DDL (database, executor, schema,
  exact current revision, exclusive backend), refusing unversioned/missing
  schemas, unexpected revisions and foreign targets,
* applies exactly ONE repository revision step with normal Alembic — never
  `upgrade head`, never an automatic chain, never a stamp, repair, downgrade,
  reset or manual definition replay — and only with the explicit `--apply`
  flag; the DEFAULT is read-only verification of the effective target,
* retains business evidence rows exactly (measured retention check),
* takes an INDEPENDENT readback manifest after the step and reports measured
  catalog deltas against explicit expectations; any drift fails the run.

Usage (each step is a separate invocation):

    python scripts/pr197_normal_migration_launcher.py \
        --dsn postgresql://USER@localhost:5432/social_manager_pr180_migration \
        --database social_manager_pr180_migration --schema SCHEMA \
        --executor starck --from-rev 0095 --to-rev 0096 --manifest-out OUT.json
    # + --apply to perform the step; without it only verification runs
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]

# The only normal chain this package permits, one step per invocation.
ALLOWED_STEPS = {("0095", "0096"), ("0096", "0097")}

# Business evidence rows that a normal migration step must retain exactly.
RETENTION_TABLES = (
    "llm_budget_days",
    "llm_budget_reservations",
    "llm_dispatch_receipts",
    "llm_outcome_events",
    "llm_attempts",
)

# Explicit expectations per step. Measured deltas are compared against these;
# nothing is assumed and nothing outside this list is required to appear, but
# every measured difference is reported in the manifest.
EXPECTED_NEW_TABLES = {"0096": set(), "0097": {"llm_budget_baselines"}}
EXPECTED_NEW_TRIGGERS = {
    "0096": set(),
    "0097": {
        "llm_budget_baselines.llm_budget_baselines_immutable",
        "llm_budget_baselines.llm_budget_baselines_no_truncate",
        "llm_budget_baselines.llm_budget_baselines_day_binding",
    },
}
EXPECTED_NEW_FUNCTIONS = {
    "0096": set(),
    "0097": {
        "reject_llm_budget_baseline_mutation()",
        "check_llm_budget_baseline_day()",
    },
}


class Stop(RuntimeError):
    """Drift/failure: stop with evidence, never retry or repair."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_dsn(dsn: str) -> str:
    """Remove credentials and host from the manifest; never log secrets."""

    if "://" not in dsn:
        return "REDACTED"
    scheme, rest = dsn.split("://", 1)
    if "@" in rest:
        rest = "REDACTED@" + rest.split("@", 1)[1]
    return scheme + "://" + rest


def row_counts(conn, schema: str, tables) -> dict[str, int]:
    counts = {}
    for table in tables:
        counts[table] = conn.execute(text(f'SELECT count(*) FROM "{schema}"."{table}"')).scalar_one()
    return counts


def finish(manifest: dict, path: str) -> int:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(manifest, handle, indent=1, sort_keys=True, default=str)
        handle.write("\n")
    print(json.dumps({"status": manifest["status"], "manifest": str(out)}, sort_keys=True))
    return 0 if manifest["status"] in ("ok", "verified-readonly") else 3


def demand(condition, label: str) -> None:
    if not condition:
        raise Stop(label)


def digest(mapping) -> str:
    return hashlib.sha256(json.dumps(mapping, sort_keys=True, default=str).encode()).hexdigest()


def object_snapshot(conn, schema: str) -> dict[str, set[str]]:
    tables = {
        row[0]
        for row in conn.exec_driver_sql(
            "SELECT table_name FROM information_schema.tables " "WHERE table_schema=%s AND table_type='BASE TABLE'",
            (schema,),
        )
    }
    triggers = {
        f"{row[0]}.{row[1]}"
        for row in conn.exec_driver_sql(
            "SELECT event_object_table, trigger_name FROM information_schema.triggers WHERE trigger_schema=%s",
            (schema,),
        )
    }
    functions = {
        row[0]
        for row in conn.exec_driver_sql(
            "SELECT p.proname||'('||pg_catalog.pg_get_function_identity_arguments(p.oid)||')' "
            "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname=%s AND p.prokind='f'",
            (schema,),
        )
    }
    constraints = {
        f"{row[0]}.{row[1]}"
        for row in conn.exec_driver_sql(
            "SELECT table_name, constraint_name FROM information_schema.table_constraints "
            "WHERE constraint_schema=%s",
            (schema,),
        )
    }
    return {"tables": tables, "triggers": triggers, "functions": functions, "constraints": constraints}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True, help="Full postgresql:// DSN of the ONLY approved target.")
    parser.add_argument("--database", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--executor", required=True)
    parser.add_argument("--from-rev", required=True, dest="from_rev")
    parser.add_argument("--to-rev", required=True, dest="to_rev")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the single step. Without this flag the launcher verifies the "
        "effective target read-only and stops (default read-only mode).",
    )
    parser.add_argument("--manifest-out", required=True, dest="manifest_out")
    args = parser.parse_args()

    manifest: dict = {
        "kind": "pr197-normal-migration",
        "at": now(),
        "step": f"{args.from_rev}->{args.to_rev}",
        "dsn": redact_dsn(args.dsn),
        "requested": {
            "database": args.database,
            "schema": args.schema,
            "executor": args.executor,
            "from_rev": args.from_rev,
            "to_rev": args.to_rev,
            "apply": bool(args.apply),
        },
        "events": [],
    }

    def event(name: str, **values) -> None:
        manifest["events"].append({"event": name, "at": now(), **values})
        print(f"[{name}] {values}", flush=True)

    try:
        demand((args.from_rev, args.to_rev) in ALLOWED_STEPS, "step-not-allowed")
        if args.database not in args.dsn:
            raise Stop("dsn-database-mismatch")

        # Suppress BOTH dotenv mechanisms: the process environment is the only
        # source, so no repository .env can redirect the effective target.
        import dotenv

        real_load_dotenv = dotenv.load_dotenv
        dotenv.load_dotenv = lambda *a, **k: False
        from pydantic_settings.sources import DotEnvSettingsSource

        real_read_env_files = DotEnvSettingsSource._read_env_files
        DotEnvSettingsSource._read_env_files = lambda self, *a, **k: {}
        os.environ["POSTGRES_URL"] = args.dsn
        os.environ["DB_SCHEMA"] = args.schema
        sys.path.insert(0, str(ROOT))

        from app.core.config import settings  # noqa: E402

        demand(settings.POSTGRES_URL == args.dsn, "effective-dsn-drift")
        demand(settings.DB_SCHEMA == args.schema, "effective-schema-drift")

        from sqlalchemy import create_engine  # noqa: E402

        engine = create_engine(args.dsn, future=True)
        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT current_database(), current_user, inet_server_addr()::text, inet_server_port()")
            ).one()
            demand(list(row[:2]) == [args.database, args.executor], "target-identity-drift")
            manifest["server"] = {"database": row[0], "user": row[1], "addr": row[2], "port": row[3]}
            event("identity-verified", **manifest["server"])

            others = conn.execute(
                text(
                    "SELECT count(*) FROM pg_catalog.pg_stat_activity "
                    "WHERE datname=current_database() AND backend_type='client backend' AND pid<>pg_backend_pid()"
                )
            ).scalar_one()
            demand(others == 0, "foreign-backend-on-target")
            event("exclusivity-verified")

            schema_exists = conn.execute(
                text("SELECT count(*) FROM pg_catalog.pg_namespace WHERE nspname=:s"), {"s": args.schema}
            ).scalar_one()
            demand(schema_exists == 1, "schema-missing-or-unversioned")
            version_table = conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema=:s AND table_name='alembic_version'"
                ),
                {"s": args.schema},
            ).scalar_one()
            demand(version_table == 1, "schema-unversioned")
            current = conn.execute(
                text(f'SELECT version_num FROM "{args.schema}".alembic_version')
            ).scalar_one_or_none()
            demand(current == args.from_rev, f"unexpected-revision:{current}")
            baselines = conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema=:s AND table_name='llm_budget_baselines'"
                ),
                {"s": args.schema},
            ).scalar_one()
            demand(baselines == 0, "baseline-present-unexpected")
            event("revision-verified", revision=current)

            before = object_snapshot(conn, args.schema)
            manifest["before"] = {"digest": digest(before), "counts": {k: len(v) for k, v in before.items()}}
            retention_before = row_counts(conn, args.schema, RETENTION_TABLES)
        # DEFAULT READ-ONLY: without --apply the launcher only verifies the
        # effective target and stops; a write requires the explicit flag.
        if not args.apply:
            manifest["status"] = "verified-readonly"
            manifest["after"] = {"revision": current, "retention": retention_before}
            event("verified-readonly", revision=current, retention=retention_before)
            return finish(manifest, args.manifest_out)

        # Normal repository Alembic, exactly one step, no stamp/head/chain.
        from alembic import command  # noqa: E402
        from alembic.config import Config  # noqa: E402

        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "migrations"))
        cfg.set_main_option("sqlalchemy.url", args.dsn)
        cfg.set_main_option("version_path_separator", "_")
        command.upgrade(cfg, args.to_rev)
        event("alembic-upgrade-ok")

        with engine.begin() as conn:
            after_rev = conn.execute(
                text(f'SELECT version_num FROM "{args.schema}".alembic_version')
            ).scalar_one_or_none()
            demand(after_rev == args.to_rev, f"readback-revision-drift:{after_rev}")
            after = object_snapshot(conn, args.schema)
            deltas = {k: sorted(after[k] - before[k]) for k in before}
            missing = {
                "tables": sorted(EXPECTED_NEW_TABLES[args.to_rev] - after["tables"]),
                "triggers": sorted(EXPECTED_NEW_TRIGGERS[args.to_rev] - after["triggers"]),
                "functions": sorted(EXPECTED_NEW_FUNCTIONS[args.to_rev] - after["functions"]),
            }
            demand(not any(missing.values()), f"expected-object-missing:{missing}")
            lost = {k: sorted(set(before[k]) - after[k]) for k in before}
            retention_after = row_counts(conn, args.schema, RETENTION_TABLES)
            demand(
                retention_after == retention_before,
                f"retention-drift:{retention_before}->{retention_after}",
            )
            manifest["after"] = {
                "revision": after_rev,
                "digest": digest(after),
                "counts": {k: len(v) for k, v in after.items()},
                "deltas": deltas,
                "lost": {k: v for k, v in lost.items() if v},
                "retention": retention_after,
            }
            event("readback-ok", revision=after_rev, deltas=deltas, retention=retention_after)
        manifest["status"] = "ok"
    except Stop as exc:
        manifest["status"] = f"stopped:{exc}"
        manifest["events"].append({"event": "stop", "at": now(), "reason": str(exc)})
        print(f"[STOP] {exc}", file=sys.stderr, flush=True)
    finally:
        try:
            dotenv.load_dotenv = real_load_dotenv
            DotEnvSettingsSource._read_env_files = real_read_env_files
        except NameError:
            pass

    return finish(manifest, args.manifest_out)


if __name__ == "__main__":
    raise SystemExit(main())
