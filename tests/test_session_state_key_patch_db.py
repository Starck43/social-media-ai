"""Prepared PostgreSQL checks; OWNER execution only, sequential.

Run: DB_TEST_SCHEMA=test_schema python test_session_state_key_patch_db.py /path/to/patched-source
POSTGRES_URL must already identify localhost:5432/social_manager. No secrets printed.
No pytest/conftest/bootstrap, schema creation, seeding, reset or migration.
Creates and removes only fresh synthetic tenants and their session rows.
NOT RUN by the remote agent; no DB acceptance or confirmation CAS claim.
"""
from __future__ import annotations
import ast
import asyncio
from copy import deepcopy
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from urllib.parse import urlsplit
from uuid import uuid4
from unittest.mock import patch

ROOT = Path(sys.argv.pop(1)).resolve() if len(sys.argv) > 1 else Path.cwd()
if os.environ.get('DB_TEST_SCHEMA') != 'test_schema':
    raise SystemExit('STOP: explicitly set DB_TEST_SCHEMA=test_schema')
url = os.environ.get('POSTGRES_URL', '')
parsed = urlsplit(url)
if parsed.hostname not in ('localhost', '127.0.0.1') or (parsed.port or 5432) != 5432 or parsed.path != '/social_manager':
    raise SystemExit('STOP: POSTGRES_URL must explicitly target localhost:5432/social_manager')
# Force the common approved database, distinct test schema; never expose its URL.
os.environ['TEST_POSTGRES_URL'] = url
sys.path.insert(0, str(ROOT))
from scripts.setup_test_db import resolve_and_redirect
resolve_and_redirect()
from app.core.config import settings
if settings.DB_SCHEMA != 'test_schema':
    raise SystemExit('STOP: redirected schema is not test_schema')
from sqlalchemy import text, update, null
from app.core import database as db_module
from app.core.database import async_engine, async_session_maker
from app.core.tenant_context import TenantContextError, tenant_scope
from app.models import AgentSession, Tenant
from app.models.managers.agent_session_manager import agent_sessions as manager
if not callable(getattr(manager, 'patch_state', None)):
    raise SystemExit('STOP: key patch is absent from the supplied source copy')


def runtime_helpers():
    names = {'_patch_session_state', '_set_pending', '_clear_pending'}
    tree = ast.parse((ROOT/'app/agent/runtime.py').read_text())
    definitions = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name in names]
    if {n.name for n in definitions} != names:
        raise RuntimeError('runtime key patch absent')
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *definitions], type_ignores=[])
    scope = {}
    exec(compile(ast.fix_missing_locations(module), 'source-runtime-helpers', 'exec'), scope)
    return scope


class SessionStateDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenants = []
        self.addAsyncCleanup(self.cleanup_owned_rows)
        # Read-only existence check. Missing tables stop before any test-row write.
        async with async_session_maker() as db:
            for model in (Tenant, AgentSession):
                exists = (await db.execute(text('SELECT to_regclass(:table_name)'), {'table_name': model.__table__.fullname})).scalar()
                if exists is None:
                    raise RuntimeError('STOP: required tables absent; no bootstrap authorized')
        for _ in range(2):
            self.tenants.append(await Tenant.objects.create(
                slug=f'state-key-{uuid4().hex}', name='Synthetic state key test', plan='business'))
        self.own, self.other = self.tenants
        with tenant_scope(self.own.id):
            self.row = await AgentSession.objects.create(
                channel='web', chat_id=f'state-key-{uuid4().hex}', state={'keep': 'existing'})

    async def cleanup_owned_rows(self):
        try:
            for tenant in reversed(self.tenants):
                await Tenant.objects.delete_by_id(tenant.id)
        finally:
            await async_engine.dispose()  # Each unittest case has its own event loop.

    async def stored(self):
        with tenant_scope(self.own.id):
            row = await manager.get(id=self.row.id)
            self.assertIsNotNone(row)
            return deepcopy(row.state)

    async def test_real_row_lock_blocks_second_writer_then_preserves_both_keys(self):
        second_pid = asyncio.Future()
        async def second_writer():
            async with async_session_maker() as db:
                try:
                    await db.execute(text("SET LOCAL lock_timeout = '8s'"))
                    second_pid.set_result((await db.execute(text('SELECT pg_backend_pid()'))).scalar_one())
                    with tenant_scope(self.own.id):
                        result = await manager.patch_state(self.row.id, {'right': 2}, session=db)
                    await db.commit()
                    return result
                except BaseException:
                    await db.rollback()
                    raise
        async with async_session_maker() as first:
            task = None
            try:
                first_pid = (await first.execute(text('SELECT pg_backend_pid()'))).scalar_one()
                with tenant_scope(self.own.id):
                    await manager.patch_state(self.row.id, {'left': 1}, session=first)
                task = asyncio.create_task(second_writer())
                pid = await asyncio.wait_for(asyncio.shield(second_pid), 5)
                # Positive PostgreSQL lock evidence, not "task still pending after sleep".
                async with async_session_maker() as observer:
                    deadline = asyncio.get_running_loop().time() + 5
                    while True:
                        blockers = (await observer.execute(text('SELECT pg_blocking_pids(:pid)'), {'pid': pid})).scalar_one()
                        if first_pid in blockers:
                            break
                        if task.done():
                            await task
                            self.fail('second writer completed before the first lock was released')
                        if asyncio.get_running_loop().time() >= deadline:
                            self.fail('PostgreSQL did not report the expected row-lock wait')
                        await asyncio.sleep(0.025)
                await first.commit()
                self.assertEqual(await asyncio.wait_for(task, 5), {'keep': 'existing', 'left': 1, 'right': 2})
            finally:
                await first.rollback()
                if task is not None:
                    if not task.done(): task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(await self.stored(), {'keep': 'existing', 'left': 1, 'right': 2})

    async def test_caller_transaction_rollback_discards_patch(self):
        async with async_session_maker() as db:
            with tenant_scope(self.own.id):
                result = await manager.patch_state(self.row.id, {'tentative': 1}, session=db)
            self.assertIn('tentative', result)
            await db.rollback()
        self.assertEqual(await self.stored(), {'keep': 'existing'})

    async def test_decorator_rolls_back_after_real_update_failure(self):
        real_factory = async_session_maker
        def factory():
            db = real_factory(); execute_original = db.execute; calls = 0
            async def execute(stmt, *args, **kwargs):
                nonlocal calls
                result = await execute_original(stmt, *args, **kwargs)
                calls += 1
                if calls == 2:  # Real locked SELECT then real UPDATE, before decorator commit.
                    raise RuntimeError('synthetic failure after state update')
                return result
            db.execute = execute
            return db
        with patch.object(db_module, 'async_session_maker', factory), tenant_scope(self.own.id):
            with self.assertRaisesRegex(RuntimeError, 'synthetic failure after state update'):
                await manager.patch_state(self.row.id, {'tentative': 1})
        self.assertEqual(await self.stored(), {'keep': 'existing'})

    async def test_foreign_tenant_cannot_patch_owned_session(self):
        with tenant_scope(self.other.id):
            result = await manager.patch_state(self.row.id, {'foreign': 1})
        self.assertIsNone(result)
        self.assertEqual(await self.stored(), {'keep': 'existing'})

    async def test_missing_context_is_closed_and_missing_row_does_not_write(self):
        with tenant_scope():
            with self.assertRaises(TenantContextError):
                await manager.patch_state(self.row.id, {'unscoped': 1})
        with tenant_scope(self.own.id):
            self.assertIsNone(await manager.patch_state(-1, {'missing': 1}))
        self.assertEqual(await self.stored(), {'keep': 'existing'})

    async def test_runtime_pending_helpers_preserve_external_key_updates(self):
        helpers = runtime_helpers()
        stale = SimpleNamespace(id=self.row.id, state={'keep': 'stale'})
        with tenant_scope(self.own.id):
            await helpers['_set_pending'](stale, {'id': 'synthetic-intent'})
        self.assertEqual(await self.stored(), {'keep': 'existing', 'pending_confirmation': {'id': 'synthetic-intent'}})
        with tenant_scope(self.own.id):
            await manager.set_state(self.row.id, keep='newer')
            await helpers['_clear_pending'](stale)
        self.assertEqual(await self.stored(), {'keep': 'newer'})
        self.assertEqual(stale.state, {'keep': 'newer'})

    async def test_null_initialization_deletion_falsy_values_and_offset(self):
        with tenant_scope(self.own.id):
            # Actual SQL NULL, not SQLAlchemy JSON's encoded JSON null.
            async with async_session_maker() as db:
                await db.execute(update(AgentSession).where(
                    AgentSession.id == self.row.id, AgentSession.tenant_id == self.own.id,
                ).values(state=null()))
                await db.commit()
            await manager.set_state(self.row.id, remove=1, zero=0, false=False, empty='')
            await manager.set_state(self.row.id, remove=None)
            await manager.set_update_offset(self.row.id, 0)
        self.assertEqual(await self.stored(), {'zero': 0, 'false': False, 'empty': '', 'update_offset': 0})


if __name__ == '__main__':
    unittest.main(verbosity=2)
