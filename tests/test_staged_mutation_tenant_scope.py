"""Actual staged-mutation/context checks with SQL/session doubles, no DB.

Run directly: python tests/test_staged_mutation_tenant_scope.py
No claim of PostgreSQL execution, source authorization or retirement acceptance.
"""
from __future__ import annotations

import ast
import asyncio
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Sequence
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
METHODS = ("record_attempts", "delete_hashes", "reset_attempts")


def load_context():
    path = ROOT / "app/core/tenant_context.py"
    tree = ast.parse(path.read_text())
    tree.body = [n for n in tree.body if not (isinstance(n, ast.ClassDef) and n.name == "PlatformScopeMiddleware")
                 and not (isinstance(n, ast.ImportFrom) and n.module == "starlette.types")]
    module = ModuleType("app.core.tenant_context")
    exec(compile(tree, str(path), "exec"), module.__dict__)
    return module


def load_methods():
    path = ROOT / "app/models/managers/collected_item_manager.py"
    tree = ast.parse(path.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CollectedItemManager")
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_staged_mutation_scope"]
    nodes.extend(n for n in cls.body if getattr(n, "name", None) in METHODS)
    namespace = {"Any": Any, "Sequence": Sequence, "settings": SimpleNamespace(DB_SCHEMA="isolated_schema"),
                 "sa_text": lambda sql: sql}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(path), "exec"), namespace)
    return {name: namespace[name] for name in METHODS}


class SessionDouble:
    def __init__(self, rowcount=2, error=None):
        self.calls = []
        self.rowcount = rowcount
        self.error = error

    async def execute(self, sql, params):
        self.calls.append((sql, dict(params)))
        await asyncio.sleep(0)
        if self.error:
            raise self.error
        return SimpleNamespace(rowcount=self.rowcount)


class StagedMutationScopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = load_context()
        self.methods = load_methods()
        patcher = patch.dict(sys.modules, {"app.core.tenant_context": self.context})
        patcher.start()
        self.addCleanup(patcher.stop)

    async def invoke(self, name, session, source_id=41, hashes=("alpha",)):
        args = (None, session, source_id) if name == "reset_attempts" else (None, session, source_id, hashes)
        return await self.methods[name](*args)

    async def assert_scoped(self, name):
        session = SessionDouble()
        with self.context.tenant_scope(17):
            self.assertEqual(await self.invoke(name, session), 2)
        sql, params = session.calls[0]
        self.assertIn("isolated_schema.collected_items", sql)
        self.assertIn("WHERE source_id = :source_id", sql)
        self.assertTrue(sql.endswith(" AND tenant_id = :tenant_id"))
        self.assertEqual((params["source_id"], params["tenant_id"]), (41, 17))
        if name == "reset_attempts":
            self.assertIn("SET analyze_attempts = 0", sql)
            self.assertIn("AND analyze_attempts > 0", sql)
        else:
            self.assertIn("AND content_hash = ANY(:hashes)", sql)
            self.assertEqual(params["hashes"], ["alpha"])
        return sql

    async def test_record_attempts_scopes_the_same_increment(self):
        sql = await self.assert_scoped("record_attempts")
        self.assertIn("SET analyze_attempts = analyze_attempts + 1", sql)

    async def test_delete_hashes_scopes_the_same_retirement_selection(self):
        sql = await self.assert_scoped("delete_hashes")
        self.assertTrue(sql.startswith("DELETE FROM "))

    async def test_reset_attempts_scopes_the_same_positive_attempt_selection(self):
        await self.assert_scoped("reset_attempts")

    async def test_missing_context_blocks_all_nonempty_mutations(self):
        for name in METHODS:
            with self.subTest(method=name):
                session = SessionDouble()
                with self.assertRaises(self.context.TenantContextError):
                    await self.invoke(name, session)
                self.assertEqual(session.calls, [])

    async def test_invalid_context_blocks_all_mutations_without_formatting(self):
        class HostileTenant:
            def __str__(self):
                raise AssertionError("Do not stringify tenant identifiers")
        for name in METHODS:
            for tenant in (True, False, 0, -1, "17", 17.0, [], {}, HostileTenant()):
                with self.subTest(method=name, kind=type(tenant).__name__):
                    session = SessionDouble()
                    with self.context.tenant_scope(tenant):
                        with self.assertRaises(self.context.TenantContextError):
                            await self.invoke(name, session)
                    self.assertEqual(session.calls, [])

    async def test_explicit_bypass_keeps_source_and_hash_predicates(self):
        for name in METHODS:
            with self.subTest(method=name):
                session = SessionDouble()
                with self.context.tenant_scope(bypass=True):
                    await self.invoke(name, session)
                sql, params = session.calls[0]
                self.assertNotIn("tenant_id", sql)
                self.assertNotIn("tenant_id", params)
                self.assertEqual(params["source_id"], 41)
                self.assertIn("WHERE source_id = :source_id", sql)
                if name != "reset_attempts":
                    self.assertEqual(params["hashes"], ["alpha"])

    async def test_empty_hash_selection_remains_no_sql_without_context(self):
        for name in ("record_attempts", "delete_hashes"):
            for hashes in ((), [], ("", None)):
                with self.subTest(method=name, hashes=hashes):
                    session = SessionDouble()
                    self.assertEqual(await self.invoke(name, session, hashes=hashes), 0)
                    self.assertEqual(session.calls, [])

    async def test_hash_order_and_deduplication_are_unchanged(self):
        for name in ("record_attempts", "delete_hashes"):
            session = SessionDouble()
            with self.context.tenant_scope(17):
                await self.invoke(name, session, hashes=("beta", "alpha", "beta", "", None))
            self.assertEqual(session.calls[0][1]["hashes"], ["beta", "alpha"])

    async def test_rowcounts_and_unknown_rowcount_return_contract_are_unchanged(self):
        for name in METHODS:
            for count in (0, 7, None):
                with self.subTest(method=name, rowcount=count):
                    with self.context.tenant_scope(17):
                        self.assertEqual(await self.invoke(name, SessionDouble(count)), count or 0)

    async def test_session_errors_propagate_without_internal_retry_or_transaction(self):
        for name in METHODS:
            session = SessionDouble(error=RuntimeError("synthetic storage failure"))
            with self.context.tenant_scope(17):
                with self.assertRaises(RuntimeError) as caught:
                    await self.invoke(name, session)
            self.assertIs(caught.exception, session.error)
            self.assertEqual(len(session.calls), 1)

    async def test_concurrent_contexts_bind_independent_tenants(self):
        async def mutate(name, tenant):
            session = SessionDouble()
            with self.context.tenant_scope(tenant):
                await asyncio.sleep(0)
                await self.invoke(name, session)
            return session.calls[0][1]["tenant_id"]
        for name in METHODS:
            with self.subTest(method=name):
                self.assertEqual(await asyncio.gather(mutate(name, 17), mutate(name, 29)), [17, 29])
        self.assertIsNone(self.context.current_tenant_id())

    async def test_nested_normal_scope_refuses_inherited_bypass(self):
        with self.context.tenant_scope(bypass=True):
            for name in METHODS:
                session = SessionDouble()
                with self.context.tenant_scope(29):
                    await self.invoke(name, session)
                self.assertEqual(session.calls[0][1]["tenant_id"], 29)
            self.assertTrue(self.context.is_bypass())


if __name__ == "__main__":
    unittest.main()
