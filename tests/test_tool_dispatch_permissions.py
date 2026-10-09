"""Actual dispatcher/policy source with infrastructure isolated; tests unrun.

Run: python tests/test_tool_dispatch_permissions.py (no DB/provider/transport).
Uses the existing boundary loader for actual policy/enums/context, not a mocked
allow/deny predicate. Pytest still uses the repository DB conftest.
"""

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]


def load_source(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


HELPERS = load_source("_dispatch_boundary_helpers", "tests/test_identity_permissions_boundary.py")


class DispatchPermissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        HELPERS.BoundaryTests.setUp(self)
        # Isolate toolset registration; handlers below are mocks, no live effects.
        with patch.dict(sys.modules, {
            "app.core.permissions": self.perms,
            "app.agent": HELPERS.module_with(toolset=HELPERS.module_with()),
        }):
            self.tools = load_source("_permission_dispatch_source", "app/agent/tools.py")
        self.handler = AsyncMock(return_value={"ok": True})

    def register(self, permission="source.update"):
        spec = self.tools.Tool(
            name="test_write", description="Test-only handler", parameters={},
            handler=self.handler, required_permission=permission,
        )
        self.tools.TOOL_REGISTRY[spec.name] = spec
        return spec

    async def test_direct_call_denies_missing_identity_before_handler(self):
        self.register()
        with self.tenant.tenant_scope(31), self.perms.permission_scope(None, is_owner=True):
            with self.assertRaises(self.perms.PermissionDeniedError):
                await self.tools.call_tool("test_write", {"value": "untrusted"})
        self.handler.assert_not_awaited()

    async def test_execute_denial_is_static_and_does_not_log_traceback(self):
        self.register()
        with patch.object(self.tools.logger, "error") as error_log:
            result = json.loads(await self.tools.execute("test_write", {"secret": "PRIVATE"}))
        self.assertEqual(result, {"error": "Permission denied", "code": "permission_denied"})
        self.assertNotIn("PRIVATE", json.dumps(result))
        error_log.assert_not_called()
        self.handler.assert_not_awaited()

    async def test_workspace_owner_cannot_reach_global_handler_in_either_path(self):
        self.register("llmmodel.create")
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user, is_owner=True):
            with self.assertRaises(self.perms.PermissionDeniedError):
                await self.tools.call_tool("test_write", {})
            result = json.loads(await self.tools.execute("test_write", {}))
        self.assertEqual(result["code"], "permission_denied")
        self.handler.assert_not_awaited()

    async def test_inactive_caller_cannot_use_declared_tool(self):
        self.register()
        self.user.is_active = False
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user, is_owner=True):
            with self.assertRaises(self.perms.PermissionDeniedError):
                await self.tools.call_tool("test_write", {})
            self.assertEqual(json.loads(await self.tools.execute("test_write", {}))["code"], "permission_denied")
        self.handler.assert_not_awaited()

    async def test_malformed_declared_right_is_not_an_undeclared_tool(self):
        with self.tenant.tenant_scope(bypass=True):
            for permission in ("", "source.unknown", "a.source.update"):
                self.register(permission)
                with self.assertRaises(self.perms.PermissionDeniedError):
                    await self.tools.call_tool("test_write", {})
                self.assertEqual(json.loads(await self.tools.execute("test_write", {}))["code"], "permission_denied")
        self.handler.assert_not_awaited()

    async def test_local_owner_allowed_and_result_contracts_preserved(self):
        self.register()
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user, is_owner=True):
            self.assertEqual(await self.tools.call_tool("test_write", {"value": 1}), {"ok": True})
            self.assertEqual(json.loads(await self.tools.execute("test_write", {"value": 2})), {"ok": True})
        self.assertEqual(self.handler.await_count, 2)

    async def test_explicit_platform_right_can_call_global_tool(self):
        self.register("llmmodel.view")
        self.user.has_perm_for = lambda model, action: model == "llmmodel" and action == self.action.VIEW
        with self.perms.permission_scope(self.user):
            self.assertEqual(await self.tools.call_tool("test_write", {}), {"ok": True})
        self.handler.assert_awaited_once_with()

    async def test_current_registry_right_and_current_subject_are_rechecked(self):
        spec = self.register("llmmodel.view")
        self.user.has_perm_for = lambda model, action: model == "llmmodel" and action == self.action.VIEW
        with self.perms.permission_scope(self.user):
            await self.tools.call_tool("test_write", {})
            spec.required_permission = "llmmodel.delete"
            with self.assertRaises(self.perms.PermissionDeniedError):
                await self.tools.call_tool("test_write", {})
            spec.required_permission = "llmmodel.view"
            self.user.has_perm_for = lambda *args: False
            with self.assertRaises(self.perms.PermissionDeniedError):
                await self.tools.call_tool("test_write", {})
        self.handler.assert_awaited_once_with()

    async def test_undeclared_right_contract_is_preserved_not_claimed_secure(self):
        self.register(None)
        self.assertEqual(await self.tools.call_tool("test_write", {}), {"ok": True})
        self.assertEqual(json.loads(await self.tools.execute("test_write", {})), {"ok": True})

    async def test_unknown_tool_and_handler_error_contracts_preserved(self):
        with self.assertRaises(LookupError):
            await self.tools.call_tool("missing", {})
        self.assertIn("error", json.loads(await self.tools.execute("missing", {})))
        self.register(None)
        self.handler.return_value = {"error": "test-only failure"}
        with self.assertRaises(self.tools.ToolError):
            await self.tools.call_tool("test_write", {})
        self.assertEqual(json.loads(await self.tools.execute("test_write", {})), {"error": "test-only failure"})


if __name__ == "__main__":
    unittest.main()
