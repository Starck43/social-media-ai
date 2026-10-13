"""Approved isolated synthetic fixture; never prepare the shared database.

The owner provisions the empty database and restricted login separately.
Only this fixture runner creates four reviewed tables and their guards.
Rehearsal rolls back; an uncertain commit must be inspected, never retried.
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
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import apply_llm_outcome_definition as application
from scripts import verify_llm_dispatch_receipts as receipts
from scripts.verify_llm_dispatch_receipts import emit, require

TARGET = ("social_manager_pr168_evidence", "test_schema", "smai_pr168_test")
TABLES = {"tenants", "llm_attempts", "llm_dispatch_receipts", "llm_outcome_events"}
FIXTURE_TIME = datetime(2026, 1, 1, 23, 59, 59, tzinfo=timezone.utc)


def validate_configuration(working_url, working_schema, explicit_url, schema):
    """Require explicit separate routing; never fall back to the common URL."""
    working, test = urlsplit(working_url), urlsplit(explicit_url)
    require(
        bool(working_url)
        and bool(explicit_url)
        and test.scheme in ("postgresql", "postgresql+asyncpg")
        and test.hostname is not None
        and working.hostname == test.hostname
        and (working.port or 5432) == (test.port or 5432)
        and working.path.lstrip("/") == "social_manager"
        and working_schema == "public",
        "Explicit isolated endpoint",
    )
    require(
        (test.path.lstrip("/"), schema, test.username) == TARGET and not test.query and not test.fragment,
        "Approved isolated database/schema/login",
    )
    return explicit_url


def resolve_target():
    from scripts.setup_test_db import resolve_test_schema, resolve_test_url, resolve_working_schema

    # Load private dotenv through the existing resolver before checking explicitness.
    resolve_test_url()
    schema = resolve_test_schema()
    url = validate_configuration(
        os.environ.get("POSTGRES_URL", "").strip(),
        resolve_working_schema(),
        os.environ.get("TEST_POSTGRES_URL", "").strip(),
        schema,
    )
    os.environ.update(POSTGRES_URL=url, DB_SCHEMA=schema, DEBUG="false")
    return url, schema


async def identity(connection):
    from sqlalchemy import text

    await application.settings_and_idle(connection)
    row = (
        await connection.execute(
            text("SELECT current_database(),current_user,current_setting('session_replication_role')")
        )
    ).one()
    require(tuple(row) == (TARGET[0], TARGET[2], "origin"), "Actual isolated identity")
    role = (
        await connection.execute(
            text(
                "SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,"
                "EXISTS(SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid) AS inherited "
                "FROM pg_roles r WHERE rolname=current_user"
            )
        )
    ).one()
    require(not any(role), "Restricted isolated login")
    db = (
        await connection.execute(
            text(
                "SELECT pg_get_userbyid(datdba),"
                "EXISTS(SELECT 1 FROM aclexplode(COALESCE(datacl,acldefault('d',datdba))) "
                "a WHERE a.grantee=0) FROM pg_database WHERE datname=current_database()"
            )
        )
    ).one()
    require(tuple(db) == (TARGET[2], False), "Private isolated database owner/ACL")
    versions = (
        (await connection.execute(text("SELECT schemaname FROM pg_tables WHERE tablename='alembic_version'")))
        .scalars()
        .all()
    )
    require(versions == [], "Fixture has no Alembic version history")


async def empty(connection):
    from sqlalchemy import text

    await identity(connection)
    tables = (
        await connection.execute(
            text(
                "SELECT schemaname,tablename FROM pg_tables "
                "WHERE schemaname NOT IN ('pg_catalog','information_schema')"
            )
        )
    ).all()
    schema = (
        await connection.execute(text("SELECT 1 FROM pg_namespace WHERE nspname=:s"), {"s": TARGET[1]})
    ).scalar_one_or_none()
    require(not tables and schema is None, "Empty isolated target; no automatic resume")


async def snapshot(connection, args):
    from sqlalchemy import text

    await identity(connection)
    tables = (
        await connection.execute(
            text(
                "SELECT schemaname,tablename FROM pg_tables "
                "WHERE schemaname NOT IN ('pg_catalog','information_schema')"
            )
        )
    ).all()
    require(set(map(tuple, tables)) == {(TARGET[1], name) for name in TABLES}, "Four-table fixture scope")
    values = {}
    for name in ("tenants", "llm_attempts", "llm_dispatch_receipts"):
        rows = (
            await connection.execute(
                text(f'SELECT id,row_to_json(t)::text AS value FROM "{TARGET[1]}"."{name}" t ORDER BY id')
            )
        ).all()
        values[name] = {
            "count": len(rows),
            "digest": receipts.digest([[r.id, json.loads(r.value, parse_float=Decimal)] for r in rows]),
        }
    if args is not None:
        require(
            values["tenants"] == {"count": args.expected_tenants, "digest": args.tenant_digest},
            "Isolated tenant baseline",
        )
        require(
            values["llm_attempts"] == {"count": args.expected_journal, "digest": args.journal_digest},
            "Isolated journal baseline",
        )
        require(
            values["llm_dispatch_receipts"] == {"count": args.expected_receipts, "digest": args.receipt_digest},
            "Isolated receipt baseline",
        )
    require(
        [values[n]["count"] for n in ("tenants", "llm_attempts", "llm_dispatch_receipts")] == [2, 0, 2],
        "Synthetic fixture counts",
    )
    ids = (await connection.execute(text(f'SELECT id FROM "{TARGET[1]}".tenants ORDER BY id'))).scalars().all()
    require(ids == [1, 94], "Synthetic fixture payers")
    guards = await receipts.catalog(connection, TARGET[1], TARGET[2])
    guards["triggers"].sort(key=lambda t: t["tgname"])
    return {"version": [], "tables": values, "receipt_guards": guards}


async def inspect_state(engine, args=None):
    from sqlalchemy import text

    async with engine.connect() as connection:
        await connection.execution_options(isolation_level="REPEATABLE READ")
        async with connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            baseline = await snapshot(connection, args)
            result = await application.catalog(connection, TARGET[1])
            return baseline, result


def receipt_revision():
    spec = importlib.util.spec_from_file_location(
        "_isolated_receipt_revision", ROOT / "migrations/versions/0093_add_llm_dispatch_receipts.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(
        module.revision == "0093" and module.down_revision == "0092" and module.schema == TARGET[1],
        "Reviewed receipt revision",
    )
    return module


def prepare(connection, models, receipt_module, outcome_module):
    """Bounded, owner-approved fixture DDL/DML on the already guarded connection."""
    from sqlalchemy import insert, text

    from app.services.ai.dispatch_receipts import build_dispatch_receipt

    tenant, attempt, receipt, _ = models
    schema = connection.dialect.identifier_preparer.quote_schema(TARGET[1])
    connection.execute(text(f"CREATE SCHEMA {schema}"))
    tenant.__table__.create(connection)
    attempt.__table__.create(connection)
    application.apply_revision(connection, receipt_module)
    application.apply_revision(connection, outcome_module)
    model = SimpleNamespace(
        id=1,
        provider=SimpleNamespace(id=1, name="synthetic-evidence-provider"),
        model_id="synthetic-evidence-model",
        input_cost_per_1k=Decimal("0.1"),
        output_cost_per_1k=Decimal("0.2"),
    )
    for payer in (1, 94):
        connection.execute(
            insert(tenant.__table__).values(
                id=payer,
                name=f"Isolated evidence {payer}",
                slug=f"isolated-evidence-{payer}",
                created_at=FIXTURE_TIME,
                updated_at=FIXTURE_TIME,
            )
        )
        value = build_dispatch_receipt(
            model,
            tenant_id=payer,
            purpose="evidence",
            stage="evidence_review",
            dispatch_id=f"{payer:032x}",
            prepared_at=FIXTURE_TIME,
        )
        connection.execute(insert(receipt.__table__).values(**value, created_at=FIXTURE_TIME))


def base_shape(connection, table):
    from sqlalchemy import inspect
    from sqlalchemy.dialects import postgresql

    inspector = inspect(connection)
    columns = inspector.get_columns(table.name, schema=TARGET[1])
    wanted = [(c.name, str(c.type.compile(dialect=postgresql.dialect())), c.nullable) for c in table.columns]
    require([(c["name"], str(c["type"]), c["nullable"]) for c in columns] == wanted, "Fixture model columns")
    require(
        inspector.get_pk_constraint(table.name, schema=TARGET[1])["constrained_columns"] == ["id"],
        "Fixture primary key",
    )
    keys = receipts.reflected_keys(inspector, table)
    return {
        "columns": [(c["name"], str(c["type"]), c["nullable"], c["default"]) for c in columns],
        "checks": inspector.get_check_constraints(table.name, schema=TARGET[1]),
        "foreign_keys": inspector.get_foreign_keys(table.name, schema=TARGET[1]),
        **keys,
    }


async def prepared_catalog(connection, models, definition):
    from sqlalchemy import text

    tenant, attempt, receipt, outcome = models
    extras = {}
    for model in (tenant, attempt):
        extras[model.__tablename__] = await connection.run_sync(lambda c, m=model: base_shape(c, m.__table__))
    await connection.run_sync(
        lambda c: receipts.reflected_parity(c, receipt.__table__, receipts.capture_definition(TARGET[1]))
    )
    await connection.run_sync(lambda c: application.reflected_shape(c, outcome.__table__, definition.table))
    result = await application.catalog(connection, TARGET[1])
    application.validate_guards(result, application.function_bodies(definition.sql), TARGET[1], TARGET[2])
    require(
        (await connection.execute(text(f'SELECT count(*) FROM "{TARGET[1]}".llm_outcome_events'))).scalar_one() == 0,
        "Fixture starts without outcomes",
    )
    return {"outcome": result, "base": extras}


async def run(state):
    from sqlalchemy import text

    from app.core.config import settings
    from app.core.database import async_engine
    from app.models import LLMAttempt, LLMDispatchReceipt, LLMOutcomeEvent, Tenant

    require(settings.DB_SCHEMA == TARGET[1] and not settings.DEBUG, "Isolated resolved schema")
    models = (Tenant, LLMAttempt, LLMDispatchReceipt, LLMOutcomeEvent)
    receipt_module, outcome_module = receipt_revision(), application.load_revision()
    definition = application.capture(outcome_module, TARGET[1])
    try:
        reference = None
        for commit in (False, True):
            application.idle_processes()
            async with async_engine.connect() as connection:
                await connection.execution_options(isolation_level="REPEATABLE READ")
                transaction = await connection.begin()
                try:
                    await empty(connection)
                    if not commit:
                        emit("preflight", target=list(TARGET), fixture_schema="absent", public_revision=None)
                    await connection.run_sync(lambda c: prepare(c, models, receipt_module, outcome_module))
                    baseline = await snapshot(connection, None)
                    catalog = await prepared_catalog(connection, models, definition)
                    current = (baseline, catalog)
                    if reference is not None:
                        require(current == reference, "Fixture differs from rollback rehearsal")
                    if commit:
                        state["commit_attempted"] = True
                        await transaction.commit()
                        state["commit_acknowledged"] = True
                        emit("application", status="COMMITTED", definition_only=True)
                    else:
                        reference = current
                        await transaction.rollback()
                        emit("rehearsal", status="PASS", rolled_back=True)
                finally:
                    if transaction.is_active:
                        await transaction.rollback()
            if not commit:
                async with async_engine.connect() as connection:
                    async with connection.begin():
                        await connection.execute(text("SET TRANSACTION READ ONLY"))
                        await empty(connection)
        async with async_engine.connect() as connection:
            await connection.execution_options(isolation_level="REPEATABLE READ")
            async with connection.begin():
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                final = (await snapshot(connection, None), await prepared_catalog(connection, models, definition))
                require(final == reference, "Post-commit fixture differs")
        emit(
            "result",
            status="FIXTURE_PASS",
            target=list(TARGET),
            provenance="isolated_synthetic_fixture",
            public_revision=None,
            outcome_rows=0,
            baseline=final[0]["tables"],
            catalog=final[1]["outcome"],
            fixture_catalog=final[1]["base"],
            fixture_matches_rehearsal=True,
            whole_gate_accepted=False,
            synthetic_and_mutation_probes_run=False,
        )
    finally:
        await async_engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--allow-isolated-fixture-preparation", action="store_true")
    args = parser.parse_args()
    state = {"commit_attempted": False, "commit_acknowledged": False}
    try:
        require(args.allow_isolated_fixture_preparation, "Separate isolated fixture approval")
        require(re.fullmatch(r"[0-9a-f]{40}", args.expected_head), "Exact head syntax")
        require(
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() == args.expected_head,
            "Exact fixture head",
        )
        require(
            not subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip(),
            "Clean fixture checkout",
        )
        application.idle_processes()
        resolve_target()
        emit("intake", head=args.expected_head, target=list(TARGET))
        asyncio.run(run(state))
        return 0
    except BaseException as error:
        emit(
            "result",
            status=application.failure_status(state),
            error_type=type(error).__name__,
            check=str(error) if isinstance(error, receipts.VerificationFailure) else None,
            **state,
            complete=False,
            do_not_rerun_automatically=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
