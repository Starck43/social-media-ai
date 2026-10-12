"""Prepared PostgreSQL CAS checks; local-owner execution only, sequential.

Run from the repository root:
DB_TEST_SCHEMA=test_schema .venv/bin/python tests/test_confirmation_consumption_db.py

Reuse the existing state-key fixture's explicit local DB/schema checks and owned
synthetic-row cleanup. No pytest/conftest/bootstrap/reset/migration/provider calls.
NOT RUN remotely. Do not use this script against production or concurrently with
another local test runner.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import unittest
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "_confirmation_owned_db_fixture",
    ROOT / "tests/test_session_state_key_patch_db.py",
)
FIXTURE = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = FIXTURE
spec.loader.exec_module(FIXTURE)
manager = FIXTURE.manager
if not callable(getattr(manager, "consume_pending_confirmation", None)):
    raise SystemExit("STOP: atomic confirmation consumption absent")


class ConfirmationConsumptionDatabaseTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = FIXTURE.SessionStateDatabaseTests.asyncSetUp
    cleanup_owned_rows = FIXTURE.SessionStateDatabaseTests.cleanup_owned_rows
    stored = FIXTURE.SessionStateDatabaseTests.stored

    async def stage(self):
        self.pending = {"intent_id": "a" * 32, "args": {"source_id": 9}}
        with FIXTURE.tenant_scope(self.own.id):
            await manager.set_state(self.row.id, pending_confirmation=self.pending, update_offset=0)

    async def test_two_backends_wait_then_exactly_one_consumption_commits(self):
        await self.stage()
        original = manager._get_owned_session
        pids = asyncio.Queue()

        @asynccontextmanager
        async def tracked():
            async with original() as db:
                await db.execute(FIXTURE.text("SET LOCAL lock_timeout = '8s'"))
                await pids.put((await db.execute(FIXTURE.text("SELECT pg_backend_pid()"))).scalar_one())
                yield db

        async def consume():
            with FIXTURE.tenant_scope(self.own.id):
                return await manager.consume_pending_confirmation(self.row.id, deepcopy(self.pending))

        tasks = []
        async with FIXTURE.async_session_maker() as locker:
            try:
                await locker.execute(
                    FIXTURE.AgentSession.__table__.select()
                    .where(
                        FIXTURE.AgentSession.id == self.row.id,
                        FIXTURE.AgentSession.tenant_id == self.own.id,
                    )
                    .with_for_update()
                )
                with patch.object(manager, "_get_owned_session", tracked):
                    tasks = [asyncio.create_task(consume()) for _ in range(2)]
                    backend_pids = [await asyncio.wait_for(pids.get(), 5) for _ in tasks]
                    self.assertEqual(len(set(backend_pids)), 2)
                    async with FIXTURE.async_session_maker() as observer:
                        deadline = asyncio.get_running_loop().time() + 5
                        while True:
                            waiting = []
                            for pid in backend_pids:
                                blockers = (
                                    await observer.execute(
                                        FIXTURE.text("SELECT pg_blocking_pids(:pid)"),
                                        {"pid": pid},
                                    )
                                ).scalar_one()
                                waiting.append(bool(blockers))
                            if all(waiting):
                                break
                            if any(task.done() for task in tasks):
                                self.fail("consumer finished while the session row was locked")
                            if asyncio.get_running_loop().time() >= deadline:
                                self.fail("PostgreSQL did not report both consumers waiting")
                            await asyncio.sleep(0.025)
                    await locker.commit()
                    results = await asyncio.wait_for(asyncio.gather(*tasks), 5)
            finally:
                await locker.rollback()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(await self.stored(), {"keep": "existing", "update_offset": 0})
        with FIXTURE.tenant_scope(self.own.id):
            self.assertIsNone(await manager.consume_pending_confirmation(self.row.id, self.pending))

    async def test_replaced_intent_survives_stale_consumer_and_preserves_other_keys(self):
        await self.stage()
        replacement = {**self.pending, "intent_id": "b" * 32}
        with FIXTURE.tenant_scope(self.own.id):
            await manager.set_state(self.row.id, pending_confirmation=replacement, keep="newer")
            self.assertIsNone(await manager.consume_pending_confirmation(self.row.id, self.pending))
        self.assertEqual(
            await self.stored(),
            {"keep": "newer", "update_offset": 0, "pending_confirmation": replacement},
        )

    async def test_foreign_missing_scope_missing_and_inactive_rows_refuse(self):
        await self.stage()
        with FIXTURE.tenant_scope(self.other.id):
            self.assertIsNone(await manager.consume_pending_confirmation(self.row.id, self.pending))
        with FIXTURE.tenant_scope():
            with self.assertRaises(FIXTURE.TenantContextError):
                await manager.consume_pending_confirmation(self.row.id, self.pending)
        with FIXTURE.tenant_scope(self.own.id):
            self.assertIsNone(await manager.consume_pending_confirmation(-1, self.pending))
            await manager.update_by_id(self.row.id, is_active=False)
            self.assertIsNone(await manager.consume_pending_confirmation(self.row.id, self.pending))
        self.assertEqual((await self.stored())["pending_confirmation"], self.pending)

    async def test_precommit_failure_rolls_back_the_removal(self):
        await self.stage()
        original = manager._get_owned_session

        @asynccontextmanager
        async def failed():
            async with original() as db:
                yield db
                raise RuntimeError("synthetic failure before commit")

        with patch.object(manager, "_get_owned_session", failed), FIXTURE.tenant_scope(self.own.id):
            with self.assertRaises(RuntimeError):
                await manager.consume_pending_confirmation(self.row.id, self.pending)
        self.assertEqual((await self.stored())["pending_confirmation"], self.pending)

    async def test_committed_but_unacknowledged_removal_returns_no_dispatch_authority(self):
        await self.stage()
        original = manager._get_owned_session

        @asynccontextmanager
        async def unacknowledged():
            async with original() as db:
                commit = db.commit

                async def uncertain_commit():
                    await commit()
                    raise RuntimeError("synthetic acknowledgement loss after real commit")

                db.commit = uncertain_commit
                yield db

        with patch.object(manager, "_get_owned_session", unacknowledged), FIXTURE.tenant_scope(self.own.id):
            with self.assertRaises(RuntimeError):
                await manager.consume_pending_confirmation(self.row.id, self.pending)
        self.assertNotIn("pending_confirmation", await self.stored())


if __name__ == "__main__":
    unittest.main(verbosity=2)
