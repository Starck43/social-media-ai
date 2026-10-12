"""Owner-local journal evidence on an approved, already prepared test target.

Standalone entry point: deliberately does NOT use pytest/conftest, seed, DDL,
migration or setup_test_db's database preparation. Existing test tenants and
tables are required. Successful checks retain a few synthetic immutable rows;
there is no automatic cleanup, reset or delete. No provider/runtime is started.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

# Support the documented direct-file invocation from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def validate_target(
    working_url: str,
    working_schema: str,
    test_url: str,
    test_schema: str,
    expected_database: str,
    expected_schema: str,
) -> None:
    """Conservative, configuration-only guard; not an endpoint alias proof."""
    working = urlsplit(working_url)
    test = urlsplit(test_url)
    database = test.path.lstrip("/")
    if test.scheme not in ("postgresql", "postgresql+asyncpg") or not test.hostname or not database:
        raise ValueError("A PostgreSQL test target is required")
    if database != expected_database or test_schema != expected_schema:
        raise ValueError("Test target does not match the approved database/schema")
    if not working_url or not working_schema or not test_schema:
        raise ValueError("Working and test target isolation must be explicit")
    # Like the existing redirect, fail conservatively even for different hosts
    # when the database and schema names coincide.
    if working.path.lstrip("/") == database and working_schema == test_schema:
        raise ValueError("Test target overlaps the working database/schema")


class InjectedTransactionFailure(RuntimeError):
    """A test-only transaction boundary fault, not a network failure claim."""


async def verify(args) -> list[str]:
    # main() must validate/redirect BEFORE these application imports: settings
    # and model schemas are frozen at import time.
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from app.core.database import async_engine
    from app.core.tenant_context import TenantContextError, tenant_scope
    from app.models import LLMAttempt, Tenant
    from app.models.managers.llm_attempt_manager import AttemptEvidenceConflict, LLMAttemptManager

    checks = []
    run_id = uuid4().hex

    def record(**changes):
        value = {
            "attempt_key": uuid4().hex + run_id,
            "purpose": "persistence_check",
            "stage": "synthetic",
            "model_id": "synthetic-no-provider-call",
            "cost_usd": Decimal("0.000000001"),
            "cost_certainty": "priced",
            "outcome": "unknown",
        }
        value.update(changes)
        return value

    def require(condition, message):
        if not condition:
            raise AssertionError(message)

    async def stored(key):
        return await LLMAttempt.objects.get(attempt_key=key)

    async def racing_writes(first, second):
        # Drain both tasks before surfacing a failure; never leave a writer
        # running while the local executor decides whether another run is safe.
        return await asyncio.gather(
            LLMAttempt.objects.record_attempt(first),
            LLMAttempt.objects.record_attempt(second),
            return_exceptions=True,
        )

    try:
        with tenant_scope(bypass=True):
            for tenant_id in (args.tenant_id, args.other_tenant_id):
                require(await Tenant.objects.get(id=tenant_id) is not None, "Required existing test tenant is missing")
        with tenant_scope(args.tenant_id):
            # Read-only preflight through the existing manager's owned session.
            # A missing table/constraint is a blocker, never permission for DDL.
            async with LLMAttempt.objects._get_owned_session() as session:
                columns = (
                    await session.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = :schema AND table_name = 'llm_attempts'"
                        ),
                        {"schema": args.expected_schema},
                    )
                ).scalars().all()
                require(
                    {column.name for column in LLMAttempt.__table__.columns}.issubset(set(columns)),
                    "Existing journal table does not match the model",
                )
                key_columns = (
                    await session.execute(
                        text(
                            "SELECT k.column_name FROM information_schema.table_constraints t "
                            "JOIN information_schema.key_column_usage k "
                            "ON k.constraint_catalog = t.constraint_catalog "
                            "AND k.constraint_schema = t.constraint_schema AND k.constraint_name = t.constraint_name "
                            "AND k.table_name = t.table_name "
                            "WHERE t.table_schema = :schema AND t.table_name = 'llm_attempts' "
                            "AND t.constraint_name = 'uq_llm_attempts_key' AND t.constraint_type = 'UNIQUE' "
                            "ORDER BY k.ordinal_position"
                        ),
                        {"schema": args.expected_schema},
                    )
                ).scalars().all()
                require(
                    key_columns == ["attempt_key"], "Required unique attempt-key constraint is missing or different"
                )
            checks.append("existing_schema_and_key_constraint")

            same = record()
            results = await racing_writes(dict(same), dict(same))
            require(
                sum(result is True for result in results) == 1 and sum(result is False for result in results) == 1,
                "Identical insert race did not acknowledge one insert and one exact replay",
            )
            require(await LLMAttempt.objects.count(attempt_key=same["attempt_key"]) == 1, "Race duplicated evidence")
            require(await LLMAttempt.objects.record_attempt(same) is False, "Late replay was not idempotent")
            checks.append("identical_insert_race_and_late_replay")

            different = record()
            changed = dict(different, cost_usd=Decimal("0.000000002"), outcome="failed_output")
            results = await racing_writes(different, changed)
            require(
                sum(result is True for result in results) == 1
                and sum(isinstance(result, AttemptEvidenceConflict) for result in results) == 1,
                "Different-evidence race did not surface a conflict",
            )
            winner = different if results[0] is True else changed
            row = await stored(different["attempt_key"])
            require(row.cost_usd == winner["cost_usd"] and row.outcome == winner["outcome"], "Evidence was rewritten")
            checks.append("different_price_and_outcome_race")

            # NOT NULL is unrelated to the suppressed unique constraint.
            invalid = record(outcome=None)
            try:
                await LLMAttempt.objects.record_attempt(invalid)
            except IntegrityError:
                pass
            else:
                raise AssertionError("Unrelated NOT NULL failure was suppressed")
            require(await stored(invalid["attempt_key"]) is None, "Failed insert persisted evidence")
            checks.append("unrelated_integrity_error_and_rollback")

            mismatch = record(tenant_id=args.other_tenant_id)
            try:
                await LLMAttempt.objects.record_attempt(mismatch)
            except TenantContextError:
                pass
            else:
                raise AssertionError("Explicit payer mismatch was accepted")
            checks.append("explicit_payer_mismatch")

            rollback_manager = LLMAttemptManager()
            owned = rollback_manager._get_owned_session

            @asynccontextmanager
            async def fail_before_commit():
                async with owned() as session:
                    yield session
                    raise InjectedTransactionFailure("Injected before owned commit")

            rollback_manager._get_owned_session = fail_before_commit
            rolled_back = record()
            try:
                await rollback_manager.record_attempt(rolled_back)
            except InjectedTransactionFailure:
                pass
            else:
                raise AssertionError("Pre-commit fault was acknowledged")
            require(await stored(rolled_back["attempt_key"]) is None, "Owned rollback left a committed row")
            checks.append("injected_before_commit_rollback")

            ack_manager = LLMAttemptManager()
            acknowledged_owned = ack_manager._get_owned_session

            @asynccontextmanager
            async def fail_after_commit():
                async with acknowledged_owned() as session:
                    yield session
                raise InjectedTransactionFailure("Injected after owned commit")

            ack_manager._get_owned_session = fail_after_commit
            uncertain = record()
            try:
                await ack_manager.record_attempt(uncertain)
            except InjectedTransactionFailure:
                pass
            else:
                raise AssertionError("Post-commit fault was acknowledged")
            require(await stored(uncertain["attempt_key"]) is not None, "Committed row was lost")
            require(
                await LLMAttempt.objects.record_attempt(uncertain) is False, "Commit-uncertain replay duplicated row"
            )
            checks.append("injected_after_commit_ack_loss_and_exact_replay")

        with tenant_scope(args.other_tenant_id):
            require(await stored(same["attempt_key"]) is None, "Other payer can read the evidence")
            try:
                await LLMAttempt.objects.record_attempt(same)
            except AttemptEvidenceConflict:
                pass
            else:
                raise AssertionError("Other payer collision was acknowledged")
            distinct = record()
            require(await LLMAttempt.objects.record_attempt(distinct) is True, "Second tenant insert failed")
            checks.append("second_tenant_isolation_and_collision")

        with tenant_scope():
            try:
                await LLMAttempt.objects.record_attempt(record())
            except TenantContextError:
                pass
            else:
                raise AssertionError("Missing payer was accepted")
            checks.append("missing_payer")
        return checks
    finally:
        await async_engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--expected-schema", required=True)
    parser.add_argument("--tenant-id", type=int, required=True)
    parser.add_argument("--other-tenant-id", type=int, required=True)
    parser.add_argument(
        "--allow-test-writes", action="store_true", help="Only on a separately approved existing target"
    )
    args = parser.parse_args()
    if not args.allow_test_writes or min(args.tenant_id, args.other_tenant_id) <= 0:
        parser.error("Explicit approval flag and two positive existing test tenant IDs are required")
    if args.tenant_id == args.other_tenant_id:
        parser.error("Two distinct existing test tenants are required; do not create them automatically")
    try:
        from scripts.setup_test_db import resolve_test_schema, resolve_test_url, resolve_working_schema

        # Resolution loads the owner's private environment without printing it.
        test_url = resolve_test_url()
        working_url = os.environ.get("POSTGRES_URL", "").strip()
        working_schema = resolve_working_schema()
        test_schema = resolve_test_schema()
        validate_target(
            working_url, working_schema, test_url, test_schema, args.expected_database, args.expected_schema
        )
        # Do not call database preparation or pytest. Redirect explicitly after
        # guard validation; models have not been imported yet.
        os.environ["POSTGRES_URL"] = test_url
        os.environ["DB_SCHEMA"] = test_schema
        checks = asyncio.run(verify(args))
    except Exception as exc:
        # SQLAlchemy/connection exception strings can include URL, params or
        # private data. Emit the type only; inspect details locally if necessary.
        print(json.dumps({"status": "BLOCKED_OR_FAILED", "error_type": type(exc).__name__, "complete": False}))
        return 1
    print(
        json.dumps(
            {
                "status": "PASS",
                "checks": checks,
                "synthetic_rows_retained": True,
                "faults": "injected transaction boundaries, not real network loss",
                "whole_gate_accepted": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
