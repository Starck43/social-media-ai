"""Prepare or verify P9-T07 billing guard definitions in its owned test schema.

This helper applies only immutable receipt/outcome/reservation trigger definitions
emitted by migrations 0093, 0094, 0095 and the current outcome validator in 0107.
It is partial test-schema setup, not execution of a migration or version history.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
REVISIONS = ("0093", "0094", "0095", "0107")
EXPECTED_DOWN = {"0093": "0092", "0094": "0093", "0095": "0094", "0107": "0106"}
GUARD_FUNCTIONS = {
    "reject_llm_receipt_mutation",
    "reject_llm_outcome_mutation",
    "check_llm_outcome_receipt",
    "reject_llm_budget_reservation_mutation",
    "check_llm_budget_reservation_receipt",
}
TABLE_GUARDS = {
    "llm_dispatch_receipts": {
        "llm_dispatch_receipts_immutable": 27,
        "llm_dispatch_receipts_no_truncate": 34,
    },
    "llm_outcome_events": {
        "llm_outcome_events_immutable": 27,
        "llm_outcome_events_no_truncate": 34,
        "llm_outcome_events_receipt_binding": 7,
    },
    "llm_budget_reservations": {
        "llm_budget_reservations_immutable": 27,
        "llm_budget_reservations_no_truncate": 34,
        "llm_budget_reservations_receipt_binding": 7,
    },
}
TRIGGER_FUNCTIONS = {
    "llm_dispatch_receipts_immutable": "reject_llm_receipt_mutation",
    "llm_dispatch_receipts_no_truncate": "reject_llm_receipt_mutation",
    "llm_outcome_events_immutable": "reject_llm_outcome_mutation",
    "llm_outcome_events_no_truncate": "reject_llm_outcome_mutation",
    "llm_outcome_events_receipt_binding": "check_llm_outcome_receipt",
    "llm_budget_reservations_immutable": "reject_llm_budget_reservation_mutation",
    "llm_budget_reservations_no_truncate": "reject_llm_budget_reservation_mutation",
    "llm_budget_reservations_receipt_binding": "check_llm_budget_reservation_receipt",
}


class SetupError(RuntimeError):
    """A fixed-label setup refusal; never include connection or row contents."""


def require(condition: bool, label: str) -> None:
    if not condition:
        raise SetupError(label)


def read_ownership(path: Path) -> dict:
    require(path.is_file() and not path.is_symlink(), "ownership-file-unavailable")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SetupError("ownership-file-invalid") from exc
    schema = value.get("schema")
    nonce = value.get("nonce")
    require(value.get("database") == "social_manager", "ownership-database")
    require(value.get("marker") == "schema_comment", "ownership-marker-kind")
    require(isinstance(schema, str) and schema.startswith("test_p9_t07_"), "ownership-schema-prefix")
    require(re.fullmatch(r"test_p9_t07_[a-z0-9_]+", schema) is not None, "ownership-schema-name")
    require(isinstance(nonce, str) and re.fullmatch(r"[0-9a-f-]{36}", nonce) is not None, "ownership-nonce")
    require(isinstance(value.get("baseline"), dict), "ownership-baseline")
    return value


def configure_target(env_file: Path, ownership: dict):
    require(env_file.is_file() and not env_file.is_symlink(), "env-file-unavailable")
    from dotenv import load_dotenv

    load_dotenv(env_file, override=True)
    os.environ["DB_TEST_SCHEMA"] = ownership["schema"]
    os.environ["DEBUG"] = "false"
    os.environ["DB_POOL"] = "null"
    sys.path.insert(0, str(ROOT))
    from scripts.setup_test_db import resolve_and_redirect

    working_url, test_url, schema = resolve_and_redirect()
    parsed = urlsplit(test_url)
    require(
        parsed.hostname in ("localhost", "127.0.0.1")
        and (parsed.port or 5432) == 5432
        and parsed.path == "/social_manager"
        and schema == ownership["schema"],
        "isolated-target-resolution",
    )
    os.environ["POSTGRES_URL"] = test_url
    os.environ["DB_SCHEMA"] = schema
    return working_url, test_url


def collect_guard_definitions(schema: str) -> tuple[list[str], dict[str, str]]:
    """Collect source revision upgrade DDL without executing any operation."""
    statements: list[str] = []
    function_bodies: dict[str, str] = {}
    for revision in REVISIONS:
        matches = list((ROOT / "migrations" / "versions").glob(f"{revision}_*.py"))
        require(len(matches) == 1, f"revision-file-{revision}")
        spec = importlib.util.spec_from_file_location(f"_p9_t07_{revision}_guard_source", matches[0])
        require(spec is not None and spec.loader is not None, f"revision-loader-{revision}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        require(
            module.revision == revision and module.down_revision == EXPECTED_DOWN[revision],
            f"revision-identity-{revision}",
        )
        for attr in ("schema", "SCHEMA"):
            if hasattr(module, attr):
                require(getattr(module, attr) == schema, f"revision-schema-{revision}")

        class Collector:
            def get_bind(self):
                from sqlalchemy.dialects import postgresql

                return SimpleNamespace(dialect=postgresql.dialect())

            def execute(self, statement, *args, **kwargs):
                sql = str(statement).strip()
                if re.match(r"^CREATE (?:OR REPLACE )?FUNCTION ", sql) or re.match(r"^CREATE TRIGGER ", sql):
                    statements.append(sql)

            def __getattr__(self, name):
                # Table, column, index and data operations are never applied here.
                return lambda *args, **kwargs: None

        collector = Collector()
        previous = module.op
        try:
            module.op = collector
            module.upgrade()
        finally:
            module.op = previous

    allowed_relations = set(TABLE_GUARDS)
    allowed_functions = set(GUARD_FUNCTIONS)
    for statement in statements:
        function = re.match(
            r"^CREATE (?:OR REPLACE )?FUNCTION ([a-z0-9_]+)\.([a-z0-9_]+)\(\) RETURNS trigger\b",
            statement,
        )
        trigger = re.match(
            r"^CREATE TRIGGER ([a-z0-9_]+) .*? ON ([a-z0-9_]+)\.([a-z0-9_]+) "
            r".*?EXECUTE FUNCTION ([a-z0-9_]+)\.([a-z0-9_]+)\(\)$",
            statement,
        )
        require(function is not None or trigger is not None, "unrecognized-guard-definition")
        if function:
            target_schema, name = function.groups()
            require(target_schema == schema and name in allowed_functions, "function-target-not-allowed")
            body = re.search(r"\bAS \$\$(.*?)\$\$\s*$", statement, re.S)
            require(body is not None, "function-body-parse")
            function_bodies[name] = " ".join(body.group(1).split())
        else:
            trigger_name, target_schema, table, function_schema, function_name = trigger.groups()
            require(
                target_schema == schema
                and table in allowed_relations
                and trigger_name in TABLE_GUARDS[table]
                and function_schema == schema
                and TRIGGER_FUNCTIONS.get(trigger_name) == function_name,
                "trigger-target-not-allowed",
            )
    require(set(function_bodies) == GUARD_FUNCTIONS, "guard-function-set")
    return statements, function_bodies


async def row_counts(connection, schema: str) -> dict[str, int]:
    from sqlalchemy import inspect, text

    names = await connection.run_sync(lambda sync: inspect(sync).get_table_names(schema=schema))
    counts = {}
    for name in sorted(names):
        require(re.fullmatch(r"[a-z0-9_]+", name) is not None, "unexpected-table-name")
        counts[name] = (await connection.execute(text(f'SELECT count(*) FROM "{schema}"."{name}"'))).scalar_one()
    return counts


async def validate_existing_metadata(connection, schema: str) -> None:
    from sqlalchemy import inspect
    from sqlalchemy.dialects import postgresql

    from app.models import Base

    for table_name in TABLE_GUARDS:
        model = Base.metadata.tables.get(f"{schema}.{table_name}")
        require(model is not None, f"metadata-table-{table_name}")
        actual = await connection.run_sync(lambda sync, name=table_name: inspect(sync).get_columns(name, schema=schema))
        expected = [
            (column.name, str(column.type.compile(dialect=postgresql.dialect())), column.nullable)
            for column in model.columns
        ]
        found = [
            (column["name"], str(column["type"].compile(dialect=postgresql.dialect())), column["nullable"])
            for column in actual
        ]
        require(found == expected, f"metadata-column-parity-{table_name}")


async def guard_catalog(connection, schema: str):
    from sqlalchemy import text

    functions = (
        (
            await connection.execute(
                text(
                    "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname=:schema AND p.proname = ANY(:names)"
                ),
                {"schema": schema, "names": list(GUARD_FUNCTIONS)},
            )
        )
        .scalars()
        .all()
    )
    triggers = (
        (
            await connection.execute(
                text(
                    "SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema "
                    "AND c.relname = ANY(:tables) AND NOT t.tgisinternal"
                ),
                {"schema": schema, "tables": list(TABLE_GUARDS)},
            )
        )
        .scalars()
        .all()
    )
    return set(functions), set(triggers)


async def validate_guards(connection, schema: str, expected_bodies: dict[str, str]) -> None:
    from sqlalchemy import text

    for table, expected in TABLE_GUARDS.items():
        rows = (
            (
                await connection.execute(
                    text(
                        "SELECT t.tgname,t.tgtype,t.tgenabled::text AS enabled,p.proname,pn.nspname AS function_schema "
                        "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                        "JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid "
                        "JOIN pg_namespace pn ON pn.oid=p.pronamespace WHERE n.nspname=:schema "
                        "AND c.relname=:table AND NOT t.tgisinternal ORDER BY t.tgname"
                    ),
                    {"schema": schema, "table": table},
                )
            )
            .mappings()
            .all()
        )
        require({row["tgname"]: row["tgtype"] for row in rows} == expected, f"trigger-set-{table}")
        require(
            all(
                row["enabled"] == "O"
                and row["function_schema"] == schema
                and TRIGGER_FUNCTIONS.get(row["tgname"]) == row["proname"]
                for row in rows
            ),
            f"trigger-binding-{table}",
        )
    functions = (
        (
            await connection.execute(
                text(
                    "SELECT p.proname,p.prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname=:schema AND p.proname = ANY(:names)"
                ),
                {"schema": schema, "names": list(GUARD_FUNCTIONS)},
            )
        )
        .mappings()
        .all()
    )
    require({row["proname"] for row in functions} == GUARD_FUNCTIONS, "guard-function-set")
    actual_bodies = {row["proname"]: " ".join(row["prosrc"].split()) for row in functions}
    require(actual_bodies == expected_bodies, "guard-function-source-parity")


def apply_guard_definitions(connection, statements: list[str]) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    operations = Operations(MigrationContext.configure(connection))
    for statement in statements:
        operations.execute(statement)


def write_result(path: Path | None, result: dict) -> None:
    if path is not None:
        require(not path.exists() and not path.is_symlink(), "result-file-already-exists")
        path.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ownership-file", required=True, type=Path)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--result-file", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if args.result_file is not None:
        require(args.result_file.parent.is_dir(), "result-directory-unavailable")
        require(not args.result_file.exists() and not args.result_file.is_symlink(), "result-file-already-exists")
    ownership = read_ownership(args.ownership_file)
    configure_target(args.env_file, ownership)
    from sqlalchemy import text

    from app.core.config import settings
    from app.core.database import async_engine

    schema = ownership["schema"]
    require(settings.DB_SCHEMA == schema and settings.DEBUG is False, "resolved-settings")
    statements, function_bodies = collect_guard_definitions(schema)

    async def run():
        async with async_engine.begin() as connection:
            await connection.execute(text("SET LOCAL lock_timeout='5s'"))
            await connection.execute(text("SET LOCAL statement_timeout='30s'"))
            await connection.execute(text("SET LOCAL application_name='p9-t07-schema-guards'"))
            identity = (await connection.execute(text("SELECT current_database(),current_user"))).one()
            require(identity[0] == ownership["database"] == "social_manager", "database-identity")
            marker = (
                await connection.execute(
                    text("SELECT obj_description(n.oid, 'pg_namespace') FROM pg_namespace n WHERE n.nspname=:schema"),
                    {"schema": schema},
                )
            ).scalar_one_or_none()
            require(marker == f"codex-p9-t07-owned:{ownership['nonce']}", "schema-nonce-comment")
            active = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                        "AND pid<>pg_backend_pid() AND state='active'"
                    )
                )
            ).scalar_one()
            require(active == 0, "database-writer-not-quiescent")
            await validate_existing_metadata(connection, schema)
            before = await row_counts(connection, schema)
            existing_functions, existing_triggers = await guard_catalog(connection, schema)
            expected_triggers = set(TRIGGER_FUNCTIONS)
            if args.check_only:
                require(existing_functions == GUARD_FUNCTIONS, "guard-functions-incomplete")
                require(existing_triggers == expected_triggers, "guard-triggers-incomplete")
                await validate_guards(connection, schema, function_bodies)
            else:
                require(not existing_functions and not existing_triggers, "partial-or-existing-guards-stop")
                await connection.run_sync(lambda sync: apply_guard_definitions(sync, statements))
                await validate_guards(connection, schema, function_bodies)
            after = await row_counts(connection, schema)
            require(after == before, "row-count-change")
        await async_engine.dispose()
        write_result(
            args.result_file,
            {
                "kind": "p9-t07-schema-guard-evidence",
                "database": ownership["database"],
                "schema": schema,
                "mode": "check-only" if args.check_only else "partial-guard-install",
                "before_counts": before,
                "after_counts": after,
                "row_counts_unchanged": before == after,
                "migration_history_changed": False,
            },
        )

    import asyncio

    asyncio.run(run())


if __name__ == "__main__":
    try:
        main()
    except SetupError as exc:
        raise SystemExit(f"STOP: {exc}")
