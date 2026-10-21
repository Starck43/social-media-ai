"""OWNER ONLY: read-only PostgreSQL evaluation of the actual prune predicate.

Run sequentially with DB_TEST_SCHEMA=test_schema and the project virtualenv.
Uses the existing localhost:5432/social_manager target after the shared guard.
SELECT over VALUES only: no app-table reads/writes, synthetic inserts, DELETE,
schema operations, providers or live handlers. This checks PostgreSQL NULL/exact
match semantics, not actual deletion, cancellation races or commit acknowledgements.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from test_job_claim_heartbeat_db import validate_environment  # noqa: E402


async def execute():
    # All application/engine imports follow validate_environment() in main.
    from sqlalchemy import DateTime, Integer, String, Text, column, event, not_, or_, select, values
    from test_cancelled_job_recovery import BUDGET, POLICY, load_prune

    from app.core.config import settings
    from app.core.database import async_engine

    if settings.DB_SCHEMA != "test_schema":
        raise RuntimeError("Stop: app schema differs from approved test_schema")
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=90)
    fixture = (
        values(
            column("id", Integer),
            column("tenant_id", Integer),
            column("status", String),
            column("error", Text),
            column("created_at", DateTime(timezone=True)),
            name="cancelled_recovery_fixture",
        )
        .data(
            [
                (0, 31, "failed", POLICY.OPERATOR_CANCELLED_ERROR, old),
                (1, 31, "failed", POLICY.OPERATOR_CANCELLED_ERROR, old),
                (2, 31, "failed", POLICY.OUTCOME_UNCONFIRMED_PREFIX + "old", old),
                (3, 31, "failed", BUDGET.ATTEMPT_BUDGET_STOP_PREFIX + "old", old),
                (4, 31, "failed", None, old),
                (5, 31, "failed", "", old),
                (6, 31, "failed", "known_failure", old),
                (7, 31, "done", None, old),
                (8, 31, "running", None, old),
                (9, 31, "failed", None, now),
                (10, 32, "failed", None, old),
                (11, 31, "failed", "Cancelled by operator suffix", old),
            ]
        )
        .alias("candidate")
    )
    selected = []
    commit_attempts = []
    statements = []

    def read_only_sql(connection, cursor, statement, parameters, context, executemany):
        normalized = " ".join(statement.upper().split())
        if not (normalized.startswith("SELECT ") or normalized == "SET TRANSACTION READ ONLY"):
            raise RuntimeError("Stop: only SELECT and read-only transaction setup are allowed")
        statements.append(normalized.split()[0])

    def reject_commit(connection):
        commit_attempts.append(True)
        raise RuntimeError("Stop: COMMIT forbidden in read-only predicate runner")

    engine = async_engine
    try:
        async with engine.connect() as connection:
            # Driver/dialect initialization precedes the guarded fixture query.
            event.listen(connection.sync_connection, "before_cursor_execute", read_only_sql)
            event.listen(connection.sync_connection, "commit", reject_commit)
            transaction = await connection.begin()
            try:
                await connection.exec_driver_sql("SET TRANSACTION READ ONLY")

                class Query:
                    def __init__(self):
                        self.predicates = [fixture.c.tenant_id == 31]

                    def filter(self, *expressions, **filters):
                        self.predicates.extend(expressions)
                        for key, value in filters.items():
                            if key != "status__in":
                                raise AssertionError("Unexpected prune filter")
                            self.predicates.append(fixture.c.status.in_(value))
                        return self

                    async def delete(self):
                        # Evaluate exactly the handler's candidate predicate,
                        # without executing DELETE or touching any stored row.
                        result = await connection.execute(
                            select(fixture.c.id).where(*self.predicates).order_by(fixture.c.id)
                        )
                        selected[:] = result.scalars().all()
                        return len(selected)

                prune = load_prune(
                    Query(),
                    {"created_at": fixture.c.created_at, "error": fixture.c.error},
                    SimpleNamespace(not_=not_, or_=or_),
                )
                result = await prune({"days": 7})
                if selected != [4, 5, 6, 7, 11] or result["deleted"] != 5:
                    raise AssertionError("Prune predicate selected unexpected read-only fixture candidates")
                print("PASS: exact cancellation and existing uncertainty markers excluded")
                print("PASS: NULL/empty/known errors remain eligible; suffix is not an exact cancellation")
                print("PASS: running/recent/foreign fixture rows excluded")
            finally:
                try:
                    await transaction.rollback()
                finally:
                    event.remove(connection.sync_connection, "before_cursor_execute", read_only_sql)
                    event.remove(connection.sync_connection, "commit", reject_commit)
    finally:
        await engine.dispose()
    if commit_attempts or statements.count("SET") != 1 or "SELECT" not in statements:
        raise AssertionError("Unexpected SQL or commit attempt")
    print("PASS: rollback complete; SELECT over VALUES only, no DML/DDL/COMMIT")
    print("Actual DELETE/concurrency/commit-ack behaviour is not certified by this runner")


def main():
    validate_environment()
    asyncio.run(execute())


if __name__ == "__main__":
    main()
