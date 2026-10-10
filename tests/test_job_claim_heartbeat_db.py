"""OWNER ONLY: actual PostgreSQL heartbeat predicates and rollback, no pytest.

Run sequentially: python tests/test_job_claim_heartbeat_db.py
Existing localhost:5432/social_manager/test_schema only. No bootstrap/seed helper,
DDL/reset/migration/provider/job execution. Synthetic inserts/updates stay in one
outer transaction and are rolled back; PostgreSQL sequences may still advance.
This proves predicate/storage behavior, NOT inter-connection lease/reaper races.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


def validate_environment():
    from dotenv import load_dotenv
    from scripts.setup_test_db import resolve_and_redirect

    load_dotenv(ROOT / ".env", override=False)
    # Check BOTH candidate URLs before resolving/importing any application code.
    urls = [os.environ.get("POSTGRES_URL", "")]
    if os.environ.get("TEST_POSTGRES_URL"):
        urls.append(os.environ["TEST_POSTGRES_URL"])
    for raw in urls:
        parsed = urlparse(raw)
        if (parsed.scheme not in {"postgresql", "postgresql+psycopg2", "postgresql+asyncpg"}
                or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                or (parsed.port or 5432) != 5432 or parsed.path != "/social_manager"):
            raise RuntimeError("Stop: expected approved local social_manager target; no DB action performed")
    if os.environ.get("DB_TEST_SCHEMA", "") != "test_schema":
        raise RuntimeError("Stop: explicitly set DB_TEST_SCHEMA=test_schema; no DB action performed")
    if os.environ.get("DB_SCHEMA", "public") == "test_schema":
        raise RuntimeError("Stop: working schema must differ from test_schema before redirect")
    _, _, schema = resolve_and_redirect()  # Environment-only, no ensure_test_database.
    if schema != "test_schema" or os.environ.get("DB_SCHEMA") != "test_schema":
        raise RuntimeError("Stop: test schema redirect did not match the approved target")
    print("Target: localhost:5432/social_manager, schema=test_schema; credentials omitted")


async def execute():
    # These imports happen only AFTER validate_environment in main().
    from sqlalchemy import event, select, update
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.core import database
    from app.core.config import settings
    from app.core.tenant_context import TenantContextError, tenant_scope
    from app.jobs.claim_outcomes import JobClaim
    from app.models import AgentTask, Job, Tenant
    from app.models.managers.job_manager import JobManager

    if settings.DB_SCHEMA != "test_schema":
        raise RuntimeError("Stop: app schema differs from approved test_schema")
    for model in (Tenant, Job, AgentTask):
        if model.__table__.schema != "test_schema":
            raise RuntimeError("Stop: mapped table schema is not test_schema")
    prefix = "heartbeat-" + uuid4().hex[:20]
    tenant_ids, job_ids, task_ids = [], [], []
    engine = database.async_engine
    commits = []

    def reject_commit(connection):
        commits.append(True)
        raise RuntimeError("Stop: actual database COMMIT forbidden in rollback-only runner")

    event.listen(engine.sync_engine, "commit", reject_commit)
    connection = None
    outer = None
    checks = 0
    try:
        connection = await engine.connect()
        outer = await connection.begin()
        await connection.exec_driver_sql("SET LOCAL statement_timeout = '10s'")
        await connection.exec_driver_sql("SET LOCAL lock_timeout = '3s'")
        factory = async_sessionmaker(bind=connection, class_=AsyncSession, expire_on_commit=False,
                                     join_transaction_mode="create_savepoint")
        # Session.begin releases its savepoint, never the caller's outer transaction.
        # Patch only this process's manager factory; no runtime/worker is started.
        with patch.object(database, "async_session_maker", factory):
            async with factory() as session:
                async with session.begin():
                    tenants = [Tenant(name="Heartbeat synthetic test", slug=prefix + suffix, plan="business")
                               for suffix in ("-a", "-b")]
                    session.add_all(tenants)
                    await session.flush()
                    tenant_ids[:] = [row.id for row in tenants]
                    task = AgentTask(tenant_id=tenants[0].id, name=prefix, job_type="learn",
                                     cron_expr="0 0 * * *", payload={}, is_active=False)
                    session.add(task)
                    await session.flush()
                    task_ids.append(task.id)
            own, other = tenant_ids
            manager = JobManager()

            async def make_job(tenant=own, **changes):
                values = dict(tenant_id=tenant, job_type="learn", agent_task_id=None, status="running",
                              run_at=NOW, locked_at=NOW - timedelta(minutes=40), started_at=NOW,
                              attempts=2, max_attempts=3, payload={"synthetic": prefix},
                              result={"retained": prefix}, error="retained", llm_cost=1.25)
                values.update(changes)
                async with factory() as session:
                    async with session.begin():
                        job = Job(**values)
                        session.add(job)
                        await session.flush()
                        job_ids.append(job.id)
                return job, JobClaim.capture(job) if job.status == "running" else None

            async def read(job_id):
                async with factory() as session:
                    row = await session.get(Job, job_id)
                    return {column.name: getattr(row, column.name) for column in Job.__table__.columns}

            async def renew(claim, stamp=NOW):
                with tenant_scope(own):
                    return await manager.renew_claim(claim, now=stamp)

            async def pass_check(label):
                nonlocal checks
                checks += 1
                print("OK " + str(checks) + ": " + label)

            job, claim = await make_job()
            before = await read(job.id)
            assert await renew(claim) is True
            expected = dict(before, locked_at=NOW)
            assert await read(job.id) == expected
            await pass_check("matching claim updates locked_at only, including unchanged updated_at")

            # A delayed callback must not shorten a newer heartbeat or fake claim loss.
            assert await renew(claim, NOW - timedelta(seconds=1)) is True
            assert await read(job.id) == expected
            await pass_check("out-of-order heartbeat keeps the newer locked_at and valid ownership")

            foreign, foreign_claim = await make_job(other)
            before_foreign = await read(foreign.id)
            for scope in (None, 0, -1, True, str(own), float(own), other):
                with tenant_scope(scope):
                    try:
                        await manager.renew_claim(claim, now=NOW)
                    except TenantContextError:
                        pass
                    else:
                        raise AssertionError("invalid/foreign scope must fail closed")
            assert await read(foreign.id) == before_foreign
            await pass_check("missing/malformed/foreign context fails closed")

            # Bypass is ONLY an exact full-claim UPDATE, never a global sweep/DELETE.
            with tenant_scope(bypass=True):
                assert await manager.renew_claim(claim, now=NOW + timedelta(seconds=1)) is True
            assert await read(foreign.id) == before_foreign
            await pass_check("explicit bypass remains constrained to one claim tenant/row")

            for changed_claim in (
                replace(claim, job_id=foreign.id), replace(claim, job_type="collect"),
                replace(claim, agent_task_id=task_ids[0]), replace(claim, attempts=3),
                replace(claim, started_at=NOW + timedelta(seconds=1)),
            ):
                before_own = await read(job.id)
                assert await renew(changed_claim) is False
                assert await read(job.id) == before_own
            assert await read(foreign.id) == before_foreign
            await pass_check("all persisted generation/identity mismatches refuse renewal")

            async with factory() as session:
                async with session.begin():
                    await session.execute(update(Job).where(Job.id == job.id, Job.tenant_id == own)
                                          .values(attempts=3, started_at=NOW + timedelta(seconds=2)))
            before_new = await read(job.id)
            assert await renew(claim) is False
            assert await read(job.id) == before_new
            with tenant_scope(own):
                assert await manager.renew_claim(replace(claim, attempts=3, started_at=NOW + timedelta(seconds=2)),
                                                now=NOW + timedelta(seconds=20)) is True
            await pass_check("old generation cannot heartbeat a newer acquired identity")

            task_job, task_claim = await make_job(agent_task_id=task_ids[0])
            task_before = await read(task_job.id)
            assert await renew(replace(task_claim, agent_task_id=None)) is False
            assert await read(task_job.id) == task_before
            assert await renew(task_claim) is True
            await pass_check("NULL/non-NULL task identity uses exact PostgreSQL predicate")

            for status in ("pending", "done", "failed"):
                terminal, _ = await make_job(status=status)
                snapshot = await read(terminal.id)
                forged = replace(claim, job_id=terminal.id)
                assert await renew(forged) is False
                assert await read(terminal.id) == snapshot
            await pass_check("non-running rows are not revived or rewritten")

        assert not commits
    finally:
        try:
            if outer is not None and outer.is_active:
                await outer.rollback()
            if connection is not None:
                await connection.close()
            # Read-only marker verification after rollback, no cleanup DELETE.
            if tenant_ids:
                async with engine.connect() as verify:
                    for model, ids in ((Tenant, tenant_ids), (Job, job_ids), (AgentTask, task_ids)):
                        remaining = (await verify.execute(select(model.id).where(model.id.in_(ids)))).all()
                        assert not remaining, "Synthetic rows remained after caller rollback"
                    await verify.rollback()
        finally:
            event.remove(engine.sync_engine, "commit", reject_commit)
            await engine.dispose()
    assert not commits
    checks += 1
    print("OK " + str(checks) + ": outer rollback leaves no synthetic rows, COMMIT=0")
    assert checks == 9
    print("9 checks passed; outer rollback complete; no bootstrap/DDL/global sweep/DELETE/provider actions")
    print("Sequence values may advance; inter-connection lock/reaper races and dispatcher timer remain unverified")


def main():
    validate_environment()
    asyncio.run(execute())


if __name__ == "__main__":
    main()
