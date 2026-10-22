"""Actual fixture cleanup ordering with stdlib doubles; no app/engine/DB imports.

The PostgreSQL/ASGI queue suites remain the authority for FK/cascade behavior.
This suite prevents fixing their cleanup by weakening production attribution.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[2]


class TaskRunNowCleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.events = []
        self.tenant_failure = None

        async def delete_tenant(identity):
            self.events.append(("tenant", identity))
            if self.tenant_failure:
                raise self.tenant_failure

        async def delete_user(identity):
            self.events.append(("user", identity))

        self.tenants = SimpleNamespace(delete_by_id=AsyncMock(side_effect=delete_tenant))
        self.users = SimpleNamespace(delete_user=AsyncMock(side_effect=delete_user))
        user_model = type("User", (), {"objects": self.users})
        tree = ast.parse((ROOT / "tests/jobs/test_task_run_now.py").read_text())
        cleanup = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_drop")
        scope = {"User": user_model, "tenants": self.tenants}
        exec(compile(ast.Module(body=[cleanup], type_ignores=[]), "<actual-queue-cleanup>", "exec"), scope)
        self.cleanup = scope["_drop"]

    async def test_generated_tenant_is_deleted_before_attributed_user(self):
        await self.cleanup(SimpleNamespace(id=7), 31)
        self.assertEqual(self.events, [("tenant", 31), ("user", 7)])
        self.tenants.delete_by_id.assert_awaited_once_with(31)
        self.users.delete_user.assert_awaited_once_with(7)

    async def test_tenant_failure_does_not_continue_to_user_deletion(self):
        self.tenant_failure = RuntimeError("Synthetic tenant cleanup failure")
        with self.assertRaisesRegex(RuntimeError, "Synthetic tenant"):
            await self.cleanup(SimpleNamespace(id=7), 31)
        self.assertEqual(self.events, [("tenant", 31)])
        self.users.delete_user.assert_not_awaited()

    async def test_partial_fixture_with_only_tenant_cleans_only_that_tenant(self):
        await self.cleanup(None, 31)
        self.assertEqual(self.events, [("tenant", 31)])
        self.users.delete_user.assert_not_awaited()

    async def test_partial_fixture_with_only_user_does_not_guess_a_tenant(self):
        await self.cleanup(SimpleNamespace(id=7), None)
        self.assertEqual(self.events, [("user", 7)])
        self.tenants.delete_by_id.assert_not_awaited()

    async def test_empty_fixture_cleanup_is_noop(self):
        await self.cleanup(None, None)
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()
