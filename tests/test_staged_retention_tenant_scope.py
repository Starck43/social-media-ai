"""Actual-method/context checks with SQL/session doubles, no DB or bootstrap.

Run directly: python tests/test_staged_retention_tenant_scope.py
This verifies scoped SQL construction, not PostgreSQL execution or purge acceptance.
"""
from __future__ import annotations

import ast
import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_context():
    path = ROOT / "app/core/tenant_context.py"
    tree = ast.parse(path.read_text())
    # Only standard-library context machinery, not ASGI middleware/imports.
    tree.body = [node for node in tree.body if not isinstance(node, ast.ClassDef) or node.name != "PlatformScopeMiddleware"]
    tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or node.module != "starlette.types"]
    module = ModuleType("app.core.tenant_context")
    exec(compile(tree, str(path), "exec"), module.__dict__)
    return module


def load_expiry():
    path = ROOT / "app/models/managers/collected_item_manager.py"
    tree = ast.parse(path.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CollectedItemManager")
    method = next(node for node in cls.body if getattr(node, "name", None) == "delete_older_than")
    module = ast.Module(body=[method], type_ignores=[])
    namespace = {"Any": Any, "datetime": datetime, "timedelta": timedelta, "timezone": timezone,
                 "settings": SimpleNamespace(DB_SCHEMA="isolated_schema"), "sa_text": lambda sql: sql}
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace["delete_older_than"]


class SessionDouble:
    def __init__(self, rowcount=0, error=None):
        self.calls = []
        self.rowcount = rowcount
        self.error = error

    async def execute(self, sql, params):
        self.calls.append((sql, dict(params)))
        if self.error is not None:
            raise self.error
        await asyncio.sleep(0)
        return SimpleNamespace(rowcount=self.rowcount)


class StagedRetentionScopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = load_context()
        self.expire = load_expiry()
        self.patcher = patch.dict(sys.modules, {"app.core.tenant_context": self.context})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    async def invoke(self, session, days=7):
        return await self.expire(None, session, days)

    async def test_current_tenant_is_bound_in_delete(self):
        session = SessionDouble(3)
        with self.context.tenant_scope(17):
            self.assertEqual(await self.invoke(session), 3)
        sql, params = session.calls[0]
        self.assertEqual(sql, "DELETE FROM isolated_schema.collected_items WHERE created_at < :cutoff AND tenant_id = :tenant_id")
        self.assertEqual(params["tenant_id"], 17)
        self.assertEqual(set(params), {"tenant_id", "cutoff"})

    async def test_missing_tenant_fails_before_execution(self):
        session = SessionDouble()
        with self.assertRaises(self.context.TenantContextError):
            await self.invoke(session)
        self.assertEqual(session.calls, [])

    async def test_malformed_tenants_fail_closed_without_stringifying(self):
        class DangerousTenant:
            def __str__(self):
                raise AssertionError("Tenant must not be stringified")
        for tenant in (True, False, 0, -1, "17", 17.0, [], {}, DangerousTenant()):
            with self.subTest(kind=type(tenant).__name__):
                session = SessionDouble()
                with self.context.tenant_scope(tenant):
                    with self.assertRaises(self.context.TenantContextError):
                        await self.invoke(session)
                self.assertEqual(session.calls, [])

    async def test_explicit_bypass_preserves_global_maintenance(self):
        session = SessionDouble(4)
        with self.context.tenant_scope(bypass=True):
            self.assertEqual(await self.invoke(session), 4)
        sql, params = session.calls[0]
        self.assertEqual(sql, "DELETE FROM isolated_schema.collected_items WHERE created_at < :cutoff")
        self.assertEqual(set(params), {"cutoff"})

    async def test_bypass_with_tenant_remains_explicit_global_scope(self):
        session = SessionDouble()
        with self.context.tenant_scope(17, bypass=True):
            await self.invoke(session)
        self.assertNotIn("tenant_id", session.calls[0][1])
        self.assertNotIn("tenant_id", session.calls[0][0])

    async def test_nested_normal_scope_does_not_inherit_bypass(self):
        session = SessionDouble()
        with self.context.tenant_scope(bypass=True):
            with self.context.tenant_scope(29):
                await self.invoke(session)
            self.assertTrue(self.context.is_bypass())
        self.assertEqual(session.calls[0][1]["tenant_id"], 29)

    async def test_nested_missing_scope_refuses_outer_bypass(self):
        session = SessionDouble()
        with self.context.tenant_scope(bypass=True):
            with self.context.tenant_scope():
                with self.assertRaises(self.context.TenantContextError):
                    await self.invoke(session)
        self.assertEqual(session.calls, [])

    async def test_concurrent_scopes_bind_independent_tenants(self):
        async def expire(tenant):
            session = SessionDouble()
            with self.context.tenant_scope(tenant):
                await asyncio.sleep(0)
                await self.invoke(session)
            return session.calls[0][1]["tenant_id"]
        self.assertEqual(await asyncio.gather(expire(17), expire(29)), [17, 29])
        self.assertIsNone(self.context.current_tenant_id())

    async def test_cutoff_preserves_utc_creation_age_and_days(self):
        for days in (1, 7, 30):
            with self.subTest(days=days):
                session = SessionDouble()
                before = datetime.now(timezone.utc) - timedelta(days=days)
                with self.context.tenant_scope(17):
                    await self.invoke(session, days)
                after = datetime.now(timezone.utc) - timedelta(days=days)
                cutoff = session.calls[0][1]["cutoff"]
                self.assertLessEqual(before, cutoff)
                self.assertLessEqual(cutoff, after)
                self.assertEqual(cutoff.utcoffset(), timedelta(0))
                self.assertNotIn("published_at", session.calls[0][0])

    async def test_empty_rowcount_remains_zero(self):
        for rowcount in (0, None):
            session = SessionDouble(rowcount)
            with self.context.tenant_scope(17):
                self.assertEqual(await self.invoke(session), 0)

    async def test_session_error_propagates_without_retry_or_commit(self):
        error = RuntimeError("synthetic execution failure")
        session = SessionDouble(error=error)
        with self.context.tenant_scope(17):
            with self.assertRaises(RuntimeError) as caught:
                await self.invoke(session)
        self.assertIs(caught.exception, error)
        self.assertEqual(len(session.calls), 1)

    async def test_context_restores_after_failure(self):
        with self.context.tenant_scope(17):
            with self.context.tenant_scope(29):
                with self.assertRaises(RuntimeError):
                    await self.invoke(SessionDouble(error=RuntimeError("synthetic")))
            self.assertEqual(self.context.current_tenant_id(), 17)
        self.assertIsNone(self.context.current_tenant_id())


if __name__ == "__main__":
    unittest.main()
