"""Approved test-only definition application, never bootstrap or event writes.

Rehearsal always rolls back. A separate transaction commits the same definition.
Public version and retained receipt/journal/tenant contents must stay unchanged.
An uncertain commit stops execution; no automatic DDL retry/downgrade/cleanup.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.verify_llm_dispatch_receipts import VerificationFailure
from scripts.verify_llm_dispatch_receipts import catalog as receipt_catalog
from scripts.verify_llm_dispatch_receipts import digest, emit, reflected_keys, require, table_signature

TABLE = "llm_outcome_events"
FUNCTIONS = {"reject_llm_outcome_mutation", "check_llm_outcome_receipt"}
TRIGGERS = {
    "llm_outcome_events_immutable": (27, "reject_llm_outcome_mutation"),
    "llm_outcome_events_no_truncate": (34, "reject_llm_outcome_mutation"),
    "llm_outcome_events_receipt_binding": (7, "check_llm_outcome_receipt"),
}


def competing_processes(output, own_pid):
    processes = {}
    for line in output.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            processes[int(parts[0])] = (int(parts[1]), parts[2])
    ignored = set()
    pid = own_pid
    while pid and pid not in ignored:
        ignored.add(pid)
        pid = processes.get(pid, (0, ""))[0]
    return [
        pid
        for pid, (_, command) in processes.items()
        if pid not in ignored
        and any(marker in command for marker in ("pytest", "alembic", "psql", "verify_llm", "apply_llm"))
    ]


def idle_processes():
    output = subprocess.run(["ps", "-eo", "pid=,ppid=,args="], capture_output=True, text=True, check=True).stdout
    require(not competing_processes(output, os.getpid()), "Competing runner")


def load_revision():
    spec = importlib.util.spec_from_file_location(
        "_outcome_application_revision", ROOT / "scripts/migrations/legacy_definitions/0094_add_llm_outcome_events.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(module.revision == "0094" and module.down_revision == "0093", "Revision identity")
    return module


def capture(module, schema):
    from sqlalchemy import Index, MetaData
    from sqlalchemy import Table as SQLTable
    from sqlalchemy.dialects import postgresql

    class Collector:
        def __init__(self):
            self.table = None
            self.sql = []

        def create_table(self, name, *items, **kwargs):
            require(self.table is None and name == TABLE and kwargs.get("schema") == schema, "Definition table scope")
            self.table = SQLTable(name, MetaData(), *items, **kwargs)

        def create_index(self, name, table, columns, **kwargs):
            require(table == TABLE and kwargs.get("schema") == schema, "Definition index scope")
            Index(name, *(self.table.c[c] for c in columns))

        def get_bind(self):
            return SimpleNamespace(dialect=postgresql.dialect())

        def execute(self, sql):
            self.sql.append(sql)

    collector = Collector()
    previous = module.op
    try:
        module.op = collector
        module.upgrade()
    finally:
        module.op = previous
    require(len(collector.sql) == 5, "Definition guard count")
    return collector


def function_bodies(sql):
    bodies = {}
    for statement in sql:
        if statement.startswith("CREATE FUNCTION "):
            match = re.search(r"\.([a-z_]+)\(\) RETURNS trigger.*?AS \$\$ (.*?) \$\$$", statement, re.S)
            require(match is not None, "Definition function syntax")
            bodies[match[1]] = " ".join(match[2].split())
    require(set(bodies) == FUNCTIONS, "Definition function identity")
    return bodies


def apply_revision(connection, module):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    previous = module.op
    try:
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
    finally:
        module.op = previous


def reflected_shape(connection, model, definition):
    from sqlalchemy import CheckConstraint, inspect
    from sqlalchemy.dialects import postgresql

    require(table_signature(model) == table_signature(definition), "Model/definition parity")
    inspector = inspect(connection)
    columns = inspector.get_columns(TABLE, schema=model.schema)
    actual = [
        (c["name"], str(c["type"].compile(dialect=postgresql.dialect())), c["nullable"], c["default"]) for c in columns
    ]
    expected = table_signature(model)["columns"]
    require(len(actual) == len(expected), "Column count")
    for found, wanted in zip(actual, expected):
        if wanted[0] == "id":
            require(
                found[:3] == wanted[:3] and found[3] == f"nextval('{model.schema}.{TABLE}_id_seq'::regclass)",
                "Identity type/sequence",
            )
        else:
            require(found == wanted, "Column type/default/nullability")
    require(inspector.get_pk_constraint(TABLE, schema=model.schema)["constrained_columns"] == ["id"], "Primary key")
    checks = inspector.get_check_constraints(TABLE, schema=model.schema)
    expected_checks = {c.name for c in model.constraints if isinstance(c, CheckConstraint)}
    require(
        len(checks) == len(expected_checks) and {c["name"] for c in checks} == expected_checks, "Check constraint names"
    )
    fks = inspector.get_foreign_keys(TABLE, schema=model.schema)
    require(
        len(fks) == 2
        and {
            (
                tuple(f["constrained_columns"]),
                f["referred_schema"],
                f["referred_table"],
                tuple(f["referred_columns"]),
                f["options"].get("ondelete"),
            )
            for f in fks
        }
        == {
            (("tenant_id",), model.schema, "tenants", ("id",), "RESTRICT"),
            (("dispatch_id",), model.schema, "llm_dispatch_receipts", ("dispatch_id",), "RESTRICT"),
        },
        "Foreign key parity",
    )
    reflected_keys(inspector, model)


async def settings_and_idle(connection):
    from sqlalchemy import text

    for sql in ("SET LOCAL TIME ZONE 'UTC'", "SET LOCAL lock_timeout='5s'", "SET LOCAL statement_timeout='60s'"):
        await connection.execute(text(sql))
    count = (
        await connection.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend' AND state IS DISTINCT FROM 'idle'"
            )
        )
    ).scalar_one()
    require(count == 0, "Competing database activity")


async def snapshot(connection, args):
    from sqlalchemy import text

    identity = (
        await connection.execute(
            text("SELECT current_database(),current_user,current_setting('session_replication_role')")
        )
    ).one()
    require(tuple(identity) == (args.expected_database, args.expected_role, "origin"), "Actual target identity")
    placements = (
        (
            await connection.execute(
                text("SELECT schemaname FROM pg_tables WHERE tablename='alembic_version' ORDER BY schemaname")
            )
        )
        .scalars()
        .all()
    )
    require(placements == ["public"], "Version placement")
    versions = (
        (await connection.execute(text('SELECT version_num FROM "public".alembic_version ORDER BY version_num')))
        .scalars()
        .all()
    )
    require(versions == ["0092"], "Public version")
    result = {"version": versions, "tables": {}}
    for name in ("llm_dispatch_receipts", "llm_attempts", "tenants"):
        rows = (
            await connection.execute(
                text(f'SELECT id,row_to_json(t)::text AS value FROM "{args.expected_schema}"."{name}" t ORDER BY id')
            )
        ).all()
        result["tables"][name] = {
            "count": len(rows),
            "digest": digest([[r.id, json.loads(r.value, parse_float=Decimal)] for r in rows]),
        }
    require(
        result["tables"]["llm_dispatch_receipts"] == {"count": args.expected_receipts, "digest": args.receipt_digest},
        "Receipt baseline",
    )
    require(
        result["tables"]["llm_attempts"] == {"count": args.expected_journal, "digest": args.journal_digest},
        "Journal baseline",
    )
    require(result["tables"]["tenants"]["count"] == args.expected_tenants, "Tenant count")
    ids = (
        (
            await connection.execute(
                text(f'SELECT id FROM "{args.expected_schema}".tenants WHERE id IN (1,94) ORDER BY id')
            )
        )
        .scalars()
        .all()
    )
    require(ids == [1, 94], "Existing approved tenants")
    guards = await receipt_catalog(connection, args.expected_schema, args.expected_role)
    guards["triggers"].sort(key=lambda t: t["tgname"])
    result["receipt_guards"] = guards
    return result


async def catalog(connection, schema):
    from sqlalchemy import text

    queries = {
        "relations": "SELECT c.relname,c.relkind::text,pg_get_userbyid(c.relowner) AS owner FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema AND c.relname IN ('llm_outcome_events','llm_outcome_events_id_seq') ORDER BY c.relname",
        "constraints": "SELECT conname,contype::text,convalidated,condeferrable,condeferred,pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conrelid=to_regclass(format('%I.llm_outcome_events',CAST(:schema AS text))) ORDER BY conname",
        "indexes": "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname=:schema AND tablename='llm_outcome_events' ORDER BY indexname",
        "functions": "SELECT p.proname,p.prosrc,p.pronargs,p.prosecdef,p.proconfig,p.provolatile::text,p.proparallel::text,p.proleakproof,p.prorettype::regtype::text AS returns,l.lanname,pg_get_userbyid(p.proowner) AS owner FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_language l ON l.oid=p.prolang WHERE n.nspname=:schema AND p.proname IN ('reject_llm_outcome_mutation','check_llm_outcome_receipt') ORDER BY p.proname",
        "triggers": "SELECT t.tgname,t.tgtype,t.tgenabled::text AS enabled,t.tgqual IS NULL AS no_when,t.tgnargs,encode(t.tgargs,'hex') AS args,t.tgattr::text AS columns,t.tgconstraint,p.proname,pn.nspname AS function_schema,pg_get_triggerdef(t.oid) AS definition FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace pn ON pn.oid=p.pronamespace WHERE t.tgrelid=to_regclass(format('%I.llm_outcome_events',CAST(:schema AS text))) AND NOT t.tgisinternal ORDER BY t.tgname",
    }
    return {
        name: [dict(r) for r in (await connection.execute(text(sql), {"schema": schema})).mappings()]
        for name, sql in queries.items()
    }


def absent(value):
    require(all(not rows for rows in value.values()), "Outcome objects present or partial; do not retry DDL")


def validate_guards(value, bodies, schema, role):
    require(len(value["functions"]) == 2, "Outcome function count")
    for f in value["functions"]:
        require(
            f["proname"] in bodies and " ".join(f["prosrc"].split()) == bodies[f["proname"]], "Outcome function body"
        )
        require(
            f["pronargs"] == 0
            and f["returns"] == "trigger"
            and f["lanname"] == "plpgsql"
            and not f["prosecdef"]
            and f["proconfig"] is None
            and f["owner"] == role
            and f["provolatile"] == "v"
            and f["proparallel"] == "u"
            and not f["proleakproof"],
            "Outcome function attributes",
        )
    require(
        len(value["triggers"]) == 3
        and {t["tgname"]: (t["tgtype"], t["proname"]) for t in value["triggers"]} == TRIGGERS,
        "Outcome trigger binding",
    )
    for t in value["triggers"]:
        require(
            t["enabled"] == "O"
            and t["function_schema"] == schema
            and t["no_when"]
            and t["tgnargs"] == 0
            and not t["args"]
            and not t["columns"]
            and t["tgconstraint"] == 0,
            "Outcome trigger attributes",
        )


async def application_transaction(engine, module, model, definition, args, state, *, commit, baseline, reference=None):
    from sqlalchemy import text

    idle_processes()
    async with engine.connect() as connection:
        await connection.execution_options(isolation_level="REPEATABLE READ")
        transaction = await connection.begin()
        try:
            await settings_and_idle(connection)
            require(await snapshot(connection, args) == baseline, "Baseline changed before application")
            absent(await catalog(connection, args.expected_schema))
            await connection.run_sync(lambda sync: apply_revision(sync, module))
            await connection.run_sync(lambda sync: reflected_shape(sync, model, definition.table))
            value = await catalog(connection, args.expected_schema)
            validate_guards(value, function_bodies(definition.sql), args.expected_schema, args.expected_role)
            require(
                (await connection.execute(text(f'SELECT count(*) FROM "{args.expected_schema}".{TABLE}'))).scalar_one()
                == 0,
                "Unexpected outcome rows",
            )
            if reference is not None:
                require(value == reference, "Definition differs from rollback rehearsal")
            require(await snapshot(connection, args) == baseline, "Baseline changed inside application")
            if commit:
                state["commit_attempted"] = True
                await transaction.commit()
                state["commit_acknowledged"] = True
            else:
                await transaction.rollback()
            return value
        finally:
            if transaction.is_active:
                await transaction.rollback()


async def inspect_state(engine, args, *, expect_absent, baseline=None):
    from sqlalchemy import text

    async with engine.connect() as connection:
        await connection.execution_options(isolation_level="REPEATABLE READ")
        async with connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            await settings_and_idle(connection)
            current = await snapshot(connection, args)
            if baseline is not None:
                require(current == baseline, "Retained contents/guards changed")
            value = await catalog(connection, args.expected_schema)
            if expect_absent:
                absent(value)
            return current, value


async def run(args, state):
    from app.core.config import settings
    from app.core.database import async_engine
    from app.models import LLMOutcomeEvent

    require(settings.DB_SCHEMA == args.expected_schema and not settings.DEBUG, "Resolved settings")
    try:
        module = load_revision()
        require(module.schema == args.expected_schema, "Revision schema")
        definition = capture(module, args.expected_schema)
        require(
            table_signature(LLMOutcomeEvent.__table__) == table_signature(definition.table), "Model/definition parity"
        )
        baseline, _ = await inspect_state(async_engine, args, expect_absent=True)
        emit("preflight", baseline=baseline["tables"], public_revision="0092", outcome_objects="absent")
        reference = await application_transaction(
            async_engine, module, LLMOutcomeEvent.__table__, definition, args, state, commit=False, baseline=baseline
        )
        await inspect_state(async_engine, args, expect_absent=True, baseline=baseline)
        emit("rehearsal", status="PASS", rolled_back=True)
        await application_transaction(
            async_engine,
            module,
            LLMOutcomeEvent.__table__,
            definition,
            args,
            state,
            commit=True,
            baseline=baseline,
            reference=reference,
        )
        emit("application", status="COMMITTED", definition_only=True)
        _, final = await inspect_state(async_engine, args, expect_absent=False, baseline=baseline)
        require(final == reference, "Post-commit catalog differs from rehearsal")
        emit(
            "result",
            status="APPLICATION_PASS",
            public_revision="0092",
            outcome_rows=0,
            catalog=final,
            baseline_preserved_since_preflight=True,
            whole_gate_accepted=False,
            synthetic_and_mutation_probes_run=False,
        )
    finally:
        await async_engine.dispose()


def failure_status(state):
    return (
        "COMMITTED_NEEDS_REVIEW"
        if state["commit_acknowledged"]
        else ("APPLICATION_OUTCOME_UNCERTAIN" if state["commit_attempted"] else "BLOCKED_OR_FAILED")
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "expected-head",
        "expected-database",
        "expected-schema",
        "expected-role",
        "receipt-digest",
        "journal-digest",
    ):
        parser.add_argument("--" + name, required=True)
    for name in ("expected-receipts", "expected-journal", "expected-tenants"):
        parser.add_argument("--" + name, type=int, required=True)
    parser.add_argument("--allow-definition-application", action="store_true")
    args = parser.parse_args()
    state = {"commit_attempted": False, "commit_acknowledged": False}
    try:
        require(args.allow_definition_application, "Definition application approval flag")
        require(
            (args.expected_database, args.expected_schema, args.expected_role)
            == ("social_manager", "test_schema", "starck"),
            "Approved target",
        )
        require(
            all(re.fullmatch(r"[0-9a-f]{64}", x) for x in (args.receipt_digest, args.journal_digest)),
            "Baseline digest syntax",
        )
        require(
            all(x >= 0 for x in (args.expected_receipts, args.expected_journal, args.expected_tenants)),
            "Baseline counts",
        )
        require(
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() == args.expected_head,
            "Exact head",
        )
        require(
            not subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip(), "Dirty checkout"
        )
        idle_processes()
        from scripts.setup_test_db import resolve_test_schema, resolve_test_url, resolve_working_schema
        from scripts.verify_llm_attempt_persistence import validate_target

        test_url, test_schema = resolve_test_url(), resolve_test_schema()
        validate_target(
            os.environ.get("POSTGRES_URL", "").strip(),
            resolve_working_schema(),
            test_url,
            test_schema,
            args.expected_database,
            args.expected_schema,
        )
        os.environ.update(POSTGRES_URL=test_url, DB_SCHEMA=test_schema, DEBUG="false")
        emit("intake", head=args.expected_head, python=sys.version.split()[0], schema=test_schema)
        asyncio.run(run(args, state))
        return 0
    except BaseException as exc:
        emit(
            "result",
            status=failure_status(state),
            error_type=type(exc).__name__,
            check=str(exc) if isinstance(exc, VerificationFailure) else None,
            **state,
            complete=False,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
