"""Owner-local evidence on an already approved receipt schema; never migrate.

Standalone, not pytest/bootstrap. Guards are disabled ONLY inside an always-
rollback transaction; the real manager reads those guards in that SAME session.
Only synthetic receipts for two approved existing tenants may be committed.
Retain all evidence. Faults are injected, not actual network-loss experiments.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Exact PostgreSQL deparsing of the reviewed definition. Formatting differences
# block for review; do not normalize away casts, parentheses or operators.
CHECKS = {
    "ck_llm_dispatch_receipts_identity": "CHECK (((dispatch_id)::text ~ '^[0-9a-f]{32}$'::text))",
    "ck_llm_dispatch_receipts_rates": "CHECK ((((input_cost_per_1k IS NULL) OR ((input_cost_per_1k >= (0)::numeric) AND (input_cost_per_1k <= 9999999999.99999999))) AND ((output_cost_per_1k IS NULL) OR ((output_cost_per_1k >= (0)::numeric) AND (output_cost_per_1k <= 9999999999.99999999)))))",
    "ck_llm_dispatch_receipts_tariff_snapshot": "CHECK (((((tariff_state)::text = 'undefined'::text) AND ((input_cost_per_1k IS NULL) OR (output_cost_per_1k IS NULL))) OR (((tariff_state)::text = 'free'::text) AND (input_cost_per_1k IS NOT NULL) AND (output_cost_per_1k IS NOT NULL) AND (input_cost_per_1k = (0)::numeric) AND (output_cost_per_1k = (0)::numeric)) OR (((tariff_state)::text = 'known'::text) AND (input_cost_per_1k IS NOT NULL) AND (output_cost_per_1k IS NOT NULL) AND ((input_cost_per_1k + output_cost_per_1k) > (0)::numeric))))",
    "ck_llm_dispatch_receipts_tariff_state": "CHECK (((tariff_state)::text = ANY ((ARRAY['known'::character varying, 'free'::character varying, 'undefined'::character varying])::text[])))",
    "ck_llm_dispatch_receipts_utc_day": "CHECK ((utc_day = ((prepared_at AT TIME ZONE 'UTC'::text))::date))",
}
TRIGGERS = {"llm_dispatch_receipts_immutable": 27, "llm_dispatch_receipts_no_truncate": 34}
FUNCTION_BODY = "BEGIN RAISE EXCEPTION 'Dispatch receipts are immutable' USING ERRCODE = '55000'; END;"


class VerificationFailure(AssertionError):
    """A fixed safe check label, without driver SQL or private parameters."""


def require(condition, label):
    if not condition:
        raise VerificationFailure(label)


def emit(phase, **values):
    print(json.dumps({"phase": phase, **values}, default=str), flush=True)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class InjectedBoundaryFailure(RuntimeError):
    """Synthetic boundary failure, never presented as real network loss."""


@asynccontextmanager
async def after_commit_fault(owned):
    async with owned() as session:
        yield session
    # The original context MUST have finished its real commit first.
    raise InjectedBoundaryFailure("Injected after successful owned commit")


@asynccontextmanager
async def before_commit_fault(owned):
    async with owned() as session:
        yield session
        raise InjectedBoundaryFailure("Injected before owned commit")


@asynccontextmanager
async def rollback_only(factory):
    async with factory() as session:
        try:
            yield session
        finally:
            # No normal-exit commit, even if an assertion or cancellation fires.
            await session.rollback()


async def drain_pair(first, second, timeout=20):
    tasks = [asyncio.create_task(first), asyncio.create_task(second)]
    try:
        return await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def table_signature(table):
    from sqlalchemy import CheckConstraint, ForeignKeyConstraint, PrimaryKeyConstraint, UniqueConstraint
    from sqlalchemy.dialects import postgresql

    dialect = postgresql.dialect()
    constraints = []
    for item in table.constraints:
        if isinstance(item, CheckConstraint):
            constraints.append(("check", item.name, str(item.sqltext)))
        elif isinstance(item, ForeignKeyConstraint):
            constraints.append(
                ("fk", tuple(item.column_keys), tuple(e.target_fullname for e in item.elements), item.ondelete)
            )
        elif isinstance(item, PrimaryKeyConstraint):
            constraints.append(("pk", tuple(c.name for c in item.columns)))
        elif isinstance(item, UniqueConstraint):
            constraints.append(("unique", item.name, tuple(c.name for c in item.columns)))
        else:
            raise VerificationFailure("Unexpected definition constraint")
    return {
        "columns": [
            (
                c.name,
                str(c.type.compile(dialect=dialect)),
                c.nullable,
                str(c.server_default.arg) if c.server_default else None,
            )
            for c in table.columns
        ],
        "constraints": sorted(constraints, key=repr),
        "indexes": sorted((i.name, tuple(c.name for c in i.columns), i.unique) for i in table.indexes),
    }


def capture_definition(schema):
    """Capture the reviewed revision with an UNBOUND collector, never DB op."""
    from sqlalchemy import Index, MetaData, Table
    from sqlalchemy.dialects import postgresql

    class Collector:
        def __init__(self):
            self.table = None
            self.sql = []

        def create_table(self, name, *items, **kwargs):
            require(self.table is None, "More than one definition table")
            self.table = Table(name, MetaData(), *items, **kwargs)

        def create_index(self, name, table, columns, **kwargs):
            require(table == self.table.name and kwargs["schema"] == schema, "Definition index scope")
            Index(name, *(self.table.c[c] for c in columns))

        def get_bind(self):
            return SimpleNamespace(dialect=postgresql.dialect())

        def execute(self, value):
            self.sql.append(value)

    path = ROOT / "migrations/versions/0093_add_llm_dispatch_receipts.py"
    spec = importlib.util.spec_from_file_location("_receipt_definition_capture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(module.schema == schema and module.revision == "0093", "Definition identity")
    collector = Collector()
    module.op = collector
    module.upgrade()
    require(len(collector.sql) == 3 and FUNCTION_BODY in collector.sql[0], "Definition guard SQL changed")
    require(all(name in collector.sql[n] for n, name in enumerate(TRIGGERS, start=1)), "Definition trigger SQL")
    return collector.table


async def timeouts(session):
    from sqlalchemy import text

    for statement in ("SET LOCAL lock_timeout='5s'", "SET LOCAL statement_timeout='60s'", "SET LOCAL TIME ZONE 'UTC'"):
        await session.execute(text(statement))


async def catalog(session, schema, role):
    from sqlalchemy import text

    async def rows(sql):
        return [dict(row) for row in (await session.execute(text(sql), {"schema": schema})).mappings()]

    constraints = await rows(
        "SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint "
        "WHERE conrelid=to_regclass(format('%I.llm_dispatch_receipts', :schema)) ORDER BY conname"
    )
    expected = dict(CHECKS)
    expected.update(
        {
            "llm_dispatch_receipts_pkey": "PRIMARY KEY (id)",
            "uq_llm_dispatch_receipts_identity": "UNIQUE (dispatch_id)",
            "llm_dispatch_receipts_tenant_id_fkey": f"FOREIGN KEY (tenant_id) REFERENCES {schema}.tenants(id) ON DELETE RESTRICT",
        }
    )
    require({r["conname"]: r["definition"] for r in constraints} == expected, "Constraint parity")
    functions = await rows(
        "SELECT p.prosrc, p.pronargs, p.prosecdef, p.proconfig, p.prorettype::regtype::text AS returns, "
        "p.provolatile::text AS volatility, p.proparallel::text AS parallel, p.proleakproof, "
        "l.lanname, pg_get_userbyid(p.proowner) AS owner FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_language l ON l.oid=p.prolang "
        "WHERE n.nspname=:schema AND p.proname='reject_llm_receipt_mutation'"
    )
    require(len(functions) == 1, "Guard function count")
    f = functions[0]
    require(" ".join(f["prosrc"].split()) == FUNCTION_BODY, "Guard function body")
    require(
        f["pronargs"] == 0
        and f["returns"] == "trigger"
        and f["lanname"] == "plpgsql"
        and not f["prosecdef"]
        and f["proconfig"] is None
        and f["owner"] == role
        and f["volatility"] == "v"
        and f["parallel"] == "u"
        and not f["proleakproof"],
        "Guard function attributes",
    )
    triggers = await rows(
        "SELECT t.tgname, t.tgenabled::text AS enabled, t.tgtype, t.tgargs, t.tgqual IS NULL AS no_when, "
        "t.tgnargs, t.tgisinternal, t.tgattr::text AS columns, t.tgconstraint, "
        "p.proname, pn.nspname AS function_schema "
        "FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace pn ON pn.oid=p.pronamespace "
        "WHERE t.tgrelid=to_regclass(format('%I.llm_dispatch_receipts', :schema)) AND NOT t.tgisinternal"
    )
    require({t["tgname"]: t["tgtype"] for t in triggers} == TRIGGERS, "Guard trigger types")
    for t in triggers:
        require(
            t["enabled"] == "O"
            and t["proname"] == "reject_llm_receipt_mutation"
            and t["function_schema"] == schema
            and t["no_when"]
            and t["tgnargs"] == 0
            and not t["tgargs"]
            and not t["columns"]
            and t["tgconstraint"] == 0,
            "Guard trigger binding/enabled/condition",
        )
    return {
        "constraints": constraints,
        "function": functions,
        "triggers": [{k: v for k, v in t.items() if k != "tgargs"} for t in triggers],
    }


def reflected_keys(inspector, model):
    """Compare logical constraints separately from physical indexes.

    PostgreSQL/SQLAlchemy versions can omit a UNIQUE's backing index or report
    it with duplicates_constraint. Only a proven matching duplicate is skipped;
    a standalone extra unique index must still fail parity.
    """
    from sqlalchemy import UniqueConstraint

    unique_constraints = inspector.get_unique_constraints(model.name, schema=model.schema)
    expected_unique = {
        c.name: tuple(column.name for column in c.columns)
        for c in model.constraints
        if isinstance(c, UniqueConstraint)
    }
    reflected_unique = {c["name"]: tuple(c["column_names"]) for c in unique_constraints}
    require(
        reflected_unique == expected_unique and len(unique_constraints) == len(expected_unique),
        "Unique constraint parity",
    )
    indexes = inspector.get_indexes(model.name, schema=model.schema)
    logical_indexes = []
    for index in indexes:
        duplicate = index.get("duplicates_constraint")
        if duplicate:
            require(
                duplicate in reflected_unique
                and index["name"] == duplicate
                and index["unique"]
                and tuple(index["column_names"]) == reflected_unique[duplicate],
                "Constraint backing index parity",
            )
        else:
            logical_indexes.append(index)
    require(
        sorted((i["name"], tuple(i["column_names"]), i["unique"]) for i in logical_indexes)
        == sorted((i.name, tuple(c.name for c in i.columns), i.unique) for i in model.indexes),
        "Index parity",
    )
    require(all(not i.get("dialect_options", {}).get("postgresql_where") for i in indexes), "Partial index")
    require(all(not i.get("include_columns") for i in indexes), "Unexpected covering index")
    require(
        all(i.get("dialect_options", {}).get("postgresql_using", "btree") == "btree" for i in indexes), "Index method"
    )
    return {"indexes": indexes, "unique_constraints": unique_constraints}


def reflected_parity(connection, model, definition):
    from sqlalchemy import inspect

    require(table_signature(model) == table_signature(definition), "ORM/revision mismatch")
    inspector = inspect(connection)
    columns = inspector.get_columns(model.name, schema=model.schema)
    from sqlalchemy.dialects import postgresql

    actual = [
        (c["name"], str(c["type"].compile(dialect=postgresql.dialect())), c["nullable"], c["default"]) for c in columns
    ]
    expected = table_signature(model)["columns"]
    require(len(actual) == len(expected), "Column count")
    for found, wanted in zip(actual, expected):
        if wanted[0] == "id":
            require(found[:3] == wanted[:3], "Identity type/nullability")
            require(
                found[3] == f"nextval('{model.schema}.llm_dispatch_receipts_id_seq'::regclass)", "Identity sequence"
            )
        else:
            require(found == wanted, "Column type/length/default/nullability")
    keys = reflected_keys(inspector, model)
    return {"column_count": len(columns), "columns": actual, **keys}


async def snapshot(session, schema):
    """Fingerprints begin HERE; they do not certify an earlier disabled window."""
    from sqlalchemy import text

    result = {}
    for name in ("llm_attempts", "llm_dispatch_receipts", "tenants"):
        rows = (
            await session.execute(
                text(f'SELECT id, row_to_json(t)::text AS value FROM "{schema}"."{name}" t ORDER BY id')
            )
        ).all()
        result[name] = {row.id: digest(json.loads(row.value, parse_float=Decimal)) for row in rows}
    return result


async def verify(args):
    from sqlalchemy import text

    from app.core.config import settings
    from app.core.database import async_engine, async_session_maker
    from app.core.tenant_context import tenant_scope
    from app.models import LLMDispatchReceipt
    from app.models.managers.llm_dispatch_receipt_manager import (
        LLMDispatchReceiptManager,
        ReceiptEvidenceConflict,
        ReceiptStorageUnavailable,
    )
    from app.services.ai.dispatch_receipts import build_dispatch_receipt

    schema = settings.DB_SCHEMA
    require(schema == args.expected_schema and not settings.DEBUG, "Resolved schema or SQL echo unsafe")
    table = f'"{schema}".llm_dispatch_receipts'
    run_id = uuid4().hex
    checks = []
    manifest = []

    def record(tenant=1):
        model = SimpleNamespace(
            id=1,
            model_id="synthetic-evidence-model",
            provider=SimpleNamespace(id=1, name="synthetic-evidence-provider"),
            input_cost_per_1k=Decimal("0.001"),
            output_cost_per_1k=Decimal("0.002"),
        )
        return build_dispatch_receipt(
            model,
            tenant_id=tenant,
            dispatch_id=uuid4().hex,
            purpose="evidence",
            stage="evidence_review",
            prepared_at=datetime.now(timezone.utc),
        )

    def manager():
        instance = LLMDispatchReceiptManager()
        original = instance._get_owned_session

        @asynccontextmanager
        async def bounded():
            async with original() as session:
                await timeouts(session)
                yield session

        instance._get_owned_session = bounded
        return instance

    async def stored(key):
        async with rollback_only(async_session_maker) as session:
            await timeouts(session)
            return (
                (await session.execute(text(f"SELECT * FROM {table} WHERE dispatch_id=:key"), {"key": key}))
                .mappings()
                .one_or_none()
            )

    async def keep(value):
        row = await stored(value["dispatch_id"])
        require(row is not None, "Committed evidence absent")
        require(all(row[k] == v for k, v in value.items()), "Committed payload mismatch")
        manifest.append({"id": row["id"], "dispatch_id": value["dispatch_id"], "tenant_id": value["tenant_id"]})
        emit("retained_receipt", run_id=run_id, **manifest[-1])

    async def invariants(session):
        identity = (await session.execute(text("SELECT current_database(), current_user"))).one()
        require(tuple(identity) == (args.expected_database, args.expected_role), "Actual target identity")
        versions = (
            (await session.execute(text("SELECT schemaname FROM pg_tables WHERE tablename='alembic_version'")))
            .scalars()
            .all()
        )
        require(versions == ["public"], "Version layout")
        revision = (await session.execute(text("SELECT version_num FROM public.alembic_version"))).scalar_one()
        require(revision == "0092", "Public revision changed")
        public_objects = (
            await session.execute(
                text(
                    "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND c.relname LIKE 'llm_dispatch_receipt%' "
                    "UNION ALL SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname='public' AND p.proname='reject_llm_receipt_mutation'"
                )
            )
        ).all()
        require(not public_objects, "Public receipt object")

    try:
        # Connection-level advisory lock is cooperative; local executor must also
        # ensure no pytest/runtime/other runner uses this shared target.
        async with async_engine.connect() as lock:
            locked = (await lock.execute(text("SELECT pg_try_advisory_lock(1370093)"))).scalar_one()
            require(locked, "Another cooperating verifier owns the target")
            await lock.rollback()
            try:
                async with rollback_only(async_session_maker) as session:
                    await timeouts(session)
                    await invariants(session)
                    baseline = await snapshot(session, schema)
                    require(len(baseline["llm_attempts"]) == args.expected_journal, "Journal count differs")
                    require(len(baseline["llm_dispatch_receipts"]) == args.expected_receipts, "Receipt count differs")
                    require({1, 94}.issubset(baseline["tenants"]), "Approved tenants absent")
                    definition = capture_definition(schema)
                    conn = await session.connection()
                    parity = await conn.run_sync(
                        lambda sync: reflected_parity(sync, LLMDispatchReceipt.__table__, definition)
                    )
                    guards = await catalog(session, schema, args.expected_role)
                emit("preflight", run_id=run_id, parity=parity, guards=guards, baseline_hash=digest(baseline))
                checks.append("complete_definition_model_catalog_parity")

                # Real manager checks its guards on exactly the DDL connection.
                for trigger in TRIGGERS:
                    probe = manager()
                    candidate = record()
                    async with rollback_only(async_session_maker) as session:
                        await timeouts(session)
                        await session.execute(text(f'ALTER TABLE {table} DISABLE TRIGGER "{trigger}"'))

                        @asynccontextmanager
                        async def same_transaction():
                            yield session

                        probe._get_owned_session = same_transaction
                        with tenant_scope(1):
                            try:
                                await probe.record_receipt(candidate)
                            except ReceiptStorageUnavailable:
                                pass
                            else:
                                raise VerificationFailure("Manager accepted disabled guard")
                        absent = (
                            await session.execute(
                                text(f"SELECT count(*) FROM {table} WHERE dispatch_id=:key"),
                                {"key": candidate["dispatch_id"]},
                            )
                        ).scalar_one()
                        require(absent == 0, "Disabled guard wrote evidence")
                    async with rollback_only(async_session_maker) as session:
                        await timeouts(session)
                        await catalog(session, schema, args.expected_role)
                    emit("guard_rollback", trigger=trigger, refused=True, restored=True, committed_disable=False)
                checks.append("same_session_guard_refusal_and_transaction_rollback")

                primary = record()
                with tenant_scope(1):
                    require(await manager().record_receipt(primary) is True, "Initial insert")
                await keep(primary)
                other_payload = dict(primary, tenant_id=94)
                with tenant_scope(94):
                    try:
                        await manager().record_receipt(other_payload)
                    except ReceiptEvidenceConflict:
                        pass
                    else:
                        raise VerificationFailure("Stored-key collision crossed tenant scope")
                    distinct = record(94)
                    require(await manager().record_receipt(distinct) is True, "Second payer insert")
                await keep(distinct)
                require((await stored(primary["dispatch_id"]))["tenant_id"] == 1, "Cross-tenant overwrite")
                checks.append("trusted_second_payer_stored_key_collision")

                for hook, persist in ((before_commit_fault, False), (after_commit_fault, True)):
                    candidate = record()
                    probe = manager()
                    original = probe._get_owned_session
                    probe._get_owned_session = lambda: hook(original)
                    with tenant_scope(1):
                        try:
                            await probe.record_receipt(candidate)
                        except InjectedBoundaryFailure:
                            pass
                        else:
                            raise VerificationFailure("Injected error not propagated")
                    row = await stored(candidate["dispatch_id"])
                    require((row is not None) == persist, "Fault persistence differs")
                    if persist:
                        with tenant_scope(1):
                            require(await manager().record_receipt(candidate) is False, "Lost ack replay inserted")
                        await keep(candidate)
                    emit(
                        "injected_boundary", after_successful_commit=persist, persisted=persist, real_network_loss=False
                    )
                checks.append("before_commit_rollback_and_after_commit_lost_ack_replay")

                for conflicting in (False, True):
                    first = record()
                    second = dict(first, stage="conflicting_review") if conflicting else dict(first)
                    arrived = 0
                    ready = asyncio.Event()
                    backends = []

                    async def write(value):
                        nonlocal arrived
                        probe = manager()
                        owned = probe._get_owned_session

                        @asynccontextmanager
                        async def barrier():
                            nonlocal arrived
                            async with owned() as session:
                                backends.append((await session.execute(text("SELECT pg_backend_pid()"))).scalar_one())
                                arrived += 1
                                if arrived == 2:
                                    ready.set()
                                await asyncio.wait_for(ready.wait(), 10)
                                yield session

                        probe._get_owned_session = barrier
                        with tenant_scope(1):
                            return await probe.record_receipt(value)

                    outcomes = await drain_pair(write(first), write(second))
                    require(len(set(backends)) == 2, "Race did not use two PostgreSQL connections")
                    require(sum(o is True for o in outcomes) == 1, "Race insert winner")
                    if conflicting:
                        require(sum(isinstance(o, ReceiptEvidenceConflict) for o in outcomes) == 1, "Race conflict")
                    else:
                        require(sum(o is False for o in outcomes) == 1, "Race exact replay")
                    winner = first if outcomes[0] is True else second
                    await keep(winner)
                    emit(
                        "barrier_race",
                        conflicting=conflicting,
                        outcomes=[o if isinstance(o, bool) else type(o).__name__ for o in outcomes],
                        separate_sessions=True,
                        multi_process=False,
                    )
                checks.append("barrier_synchronized_identical_and_conflicting_sessions")

                async with rollback_only(async_session_maker) as session:
                    await timeouts(session)
                    await invariants(session)
                    await catalog(session, schema, args.expected_role)
                    final = await snapshot(session, schema)
                require(final["llm_attempts"] == baseline["llm_attempts"], "Journal content changed")
                require(final["tenants"] == baseline["tenants"], "Tenant content changed")
                receipts = final["llm_dispatch_receipts"]
                require(
                    all(receipts.get(k) == v for k, v in baseline["llm_dispatch_receipts"].items()),
                    "Retained receipt changed",
                )
                require(
                    set(receipts) - set(baseline["llm_dispatch_receipts"]) == {r["id"] for r in manifest},
                    "Unexplained new rows",
                )
                checks.append("baseline_content_preservation_since_this_preflight")
                emit(
                    "result",
                    status="PASS",
                    checks=checks,
                    retained=manifest,
                    total_receipts=len(receipts),
                    baseline_integrity_since_preflight_only=True,
                    whole_gate_accepted=False,
                )
            finally:
                await lock.execute(text("SELECT pg_advisory_unlock(1370093)"))
                await lock.rollback()
    finally:
        await async_engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--expected-schema", required=True)
    parser.add_argument("--expected-role", required=True)
    parser.add_argument("--expected-receipts", type=int, default=44)
    parser.add_argument("--expected-journal", type=int, default=79)
    parser.add_argument("--allow-test-writes", action="store_true")
    parser.add_argument("--allow-rollback-guard-probes", action="store_true")
    args = parser.parse_args()
    try:
        require(args.allow_test_writes and args.allow_rollback_guard_probes, "Approval flags")
        require(args.expected_schema == "test_schema" and args.expected_database == "social_manager", "Approved target")
        require(args.expected_role == "starck", "Approved role")
        require(args.expected_receipts >= 44 and args.expected_journal == 79, "Approved baseline")
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        require(head == args.expected_head, "Exact head mismatch")
        require(
            not subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip(), "Dirty checkout"
        )
        from scripts.setup_test_db import resolve_test_schema, resolve_test_url, resolve_working_schema
        from scripts.verify_llm_attempt_persistence import validate_target

        test_url = resolve_test_url()
        test_schema = resolve_test_schema()
        validate_target(
            os.environ.get("POSTGRES_URL", "").strip(),
            resolve_working_schema(),
            test_url,
            test_schema,
            args.expected_database,
            args.expected_schema,
        )
        os.environ["POSTGRES_URL"] = test_url
        os.environ["DB_SCHEMA"] = test_schema
        os.environ["DEBUG"] = "false"
        emit("intake", head=head, python=sys.version.split()[0], schema=test_schema)
        asyncio.run(verify(args))
        return 0
    except Exception as exc:
        # Never print driver exceptions: they may include credentials/parameters.
        label = str(exc) if isinstance(exc, VerificationFailure) else None
        emit("result", status="BLOCKED_OR_FAILED", error_type=type(exc).__name__, check=label, complete=False)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
