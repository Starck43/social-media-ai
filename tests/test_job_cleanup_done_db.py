"""Prepared PostgreSQL conditional-cleanup checks; owner execution only.

Existing reviewed test schema only. No conftest/bootstrap/reset/migrations,
providers or live calls. Concurrent sessions belong to ONE sequential process.
Bypass tests are narrowed to this test's generated IDs, never retained evidence.
"""

import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.setup_test_db import resolve_and_redirect

resolve_and_redirect()  # Must precede application imports; refuses working schema.

from sqlalchemy import event, update

from app.core.database import async_engine, async_session_maker
from app.core.tenant_context import TenantContextError, tenant_scope
from app.models import Job, Tenant
from app.models.managers import base_manager as base_module
from app.models.managers import job_manager as manager_module
from app.models.managers.job_manager import JobManager

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
OLD = NOW - timedelta(hours=25)


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class CleanupDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenants = []
        self.addAsyncCleanup(self.cleanup_rows)
        self.clock = patch.object(manager_module, "datetime", FixedDatetime)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        for _ in range(2):
            self.tenants.append(
                await Tenant.objects.create(
                    name="Completed cleanup test", slug=f"completed-cleanup-{uuid4().hex}", plan="business"
                )
            )
        self.own, self.other = self.tenants

    async def cleanup_rows(self):
        try:
            for tenant in reversed(self.tenants):
                await Tenant.objects.delete_by_id(tenant.id)
        finally:
            await async_engine.dispose()

    async def make_job(self, tenant=None, **changes):
        tenant = tenant or self.own
        values = dict(
            job_type="learn",
            payload={},
            run_at=NOW,
            status="done",
            finished_at=OLD,
            attempts=1,
            max_attempts=3,
            result={"audit": "keep"},
            llm_cost=0.25,
        )
        with tenant_scope(tenant.id):
            return await Job.objects.create(**(values | changes))

    async def read(self, job, tenant=None):
        with tenant_scope((tenant or self.own).id):
            return await Job.objects.get(id=job.id)

    async def clean(self, **kwargs):
        with tenant_scope(self.own.id):
            return await JobManager().cleanup_done(**kwargs)

    async def test_tenant_status_age_boundary_and_null_are_preserved(self):
        removed = [await self.make_job(), await self.make_job()]
        kept = [
            await self.make_job(**changes)
            for changes in (
                {"status": "running"},
                {"status": "pending"},
                {"status": "failed"},
                {"finished_at": NOW - timedelta(hours=24)},
                {"finished_at": NOW},
                {"finished_at": None},
            )
        ]
        foreign = await self.make_job(self.other)
        self.assertEqual(await self.clean(), 2)
        for job in removed:
            self.assertIsNone(await self.read(job))
        for job in kept:
            stored = await self.read(job)
            self.assertEqual(
                (stored.status, stored.finished_at, stored.result, stored.llm_cost),
                (job.status, job.finished_at, job.result, job.llm_cost),
            )
        self.assertIsNotNone(await self.read(foreign, self.other))
        self.assertEqual(await self.clean(), 0)

    async def test_custom_retention_and_bypass_are_bounded_to_fixture_ids(self):
        own = await self.make_job(finished_at=NOW - timedelta(hours=2))
        other = await self.make_job(self.other, finished_at=NOW - timedelta(hours=2))
        boundary = await self.make_job(finished_at=NOW - timedelta(hours=1))
        with tenant_scope(bypass=True):
            manager = JobManager()
            manager.filter = manager.filter(id__in=[own.id, other.id, boundary.id]).filter
            self.assertEqual(await manager.cleanup_done(older_than_hours=1), 2)
        self.assertIsNone(await self.read(own))
        self.assertIsNone(await self.read(other, self.other))
        self.assertIsNotNone(await self.read(boundary))

    async def test_missing_tenant_scope_fails_closed(self):
        job = await self.make_job()
        with tenant_scope(None), self.assertRaises(TenantContextError):
            await JobManager().cleanup_done()
        self.assertIsNotNone(await self.read(job))

    async def test_two_cleaners_report_one_actual_deletion(self):
        job = await self.make_job()
        counts = await asyncio.wait_for(asyncio.gather(self.clean(), self.clean()), timeout=20)
        self.assertEqual(sorted(counts), [0, 1])
        self.assertIsNone(await self.read(job))

    async def competing_write(self, values):
        job = await self.make_job()
        issued = asyncio.Event()
        cleaner = None
        async with async_session_maker() as cleanup_session:
            connection = await cleanup_session.connection()

            def before_execute(conn, cursor, statement, parameters, context, executemany):
                if context.compiled is not None and context.compiled.isdelete:
                    issued.set()

            event.listen(connection.sync_connection, "before_cursor_execute", before_execute)
            try:
                async with async_session_maker() as holder:
                    async with holder.begin():
                        await holder.execute(
                            update(Job).where(Job.id == job.id, Job.tenant_id == self.own.id).values(**values)
                        )
                        with tenant_scope(self.own.id):
                            manager = JobManager()
                            manager.filter = manager.get_queryset(session=cleanup_session).filter
                            cleaner = asyncio.create_task(manager.cleanup_done())
                        await asyncio.wait_for(issued.wait(), timeout=5)
                    self.assertEqual(await asyncio.wait_for(asyncio.shield(cleaner), timeout=5), 0)
            finally:
                if cleaner is not None and not cleaner.done():
                    cleaner.cancel()
                    await asyncio.gather(cleaner, return_exceptions=True)
                event.remove(connection.sync_connection, "before_cursor_execute", before_execute)
        stored = await self.read(job)
        self.assertIsNotNone(stored)
        for key, value in values.items():
            self.assertEqual(getattr(stored, key), value)

    async def test_delete_rechecks_status_after_concurrent_running_transition(self):
        await self.competing_write({"status": "running"})

    async def test_delete_rechecks_completion_age_after_concurrent_refresh(self):
        await self.competing_write({"finished_at": NOW})

    async def test_delete_failure_rolls_back_without_partial_success(self):
        job = await self.make_job()

        def factory():
            session = async_session_maker()

            def after_execute(conn, cursor, statement, parameters, context, executemany):
                if context.compiled is not None and context.compiled.isdelete:
                    raise RuntimeError("injected cleanup write failure")

            event.listen(
                session.sync_session,
                "after_begin",
                lambda session, tx, conn: event.listen(conn, "after_cursor_execute", after_execute),
            )
            return session

        with patch.object(base_module, "async_session_maker", factory):
            with self.assertRaisesRegex(RuntimeError, "injected cleanup write failure"):
                await self.clean()
        self.assertIsNotNone(await self.read(job))

    async def test_committed_ack_loss_propagates_without_another_delete(self):
        job = await self.make_job()
        sessions = 0
        deletes = 0

        def factory():
            nonlocal sessions
            sessions += 1
            session = async_session_maker()
            commit = session.commit

            async def uncertain_commit():
                await commit()
                raise ConnectionError("injected cleanup acknowledgement loss")

            def after_execute(conn, cursor, statement, parameters, context, executemany):
                nonlocal deletes
                if context.compiled is not None and context.compiled.isdelete:
                    deletes += 1

            event.listen(
                session.sync_session,
                "after_begin",
                lambda session, tx, conn: event.listen(conn, "after_cursor_execute", after_execute),
            )
            session.commit = uncertain_commit
            return session

        with patch.object(base_module, "async_session_maker", factory):
            with self.assertRaisesRegex(ConnectionError, "injected cleanup acknowledgement loss"):
                await self.clean()
        self.assertEqual((sessions, deletes), (1, 1))
        self.assertIsNone(await self.read(job))  # A raised ACK error does not establish rollback.


if __name__ == "__main__":
    unittest.main()
