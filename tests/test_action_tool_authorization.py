"""Actual action registry/policy source; mocked storage, no transports. NOT RUN.

Run: python tests/test_action_tool_authorization.py.
"""

import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load_source(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


HELPERS = load_source("_action_dispatch_helpers", "tests/test_tool_dispatch_permissions.py")


class ActionToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        HELPERS.DispatchPermissionTests.setUp(self)
        module_with = HELPERS.HELPERS.module_with

        class Status(Enum):
            PENDING = "pending"
            APPROVED = "approved"

        self.status = Status
        self.action_row = SimpleNamespace(
            id=9, status=Status.PENDING, payload={"text": "preview"}, source_id=10,
            agent_scenario_id=11, action_type=SimpleNamespace(name="COMMENT"), dry_run=True,
            created_at=datetime.now(timezone.utc), confirmed_at=None, confirmed_by=None, result=None, attempts=0,
        )
        self.action_get = AsyncMock(return_value=self.action_row)
        self.source_get = AsyncMock(return_value=SimpleNamespace(id=10))
        self.scenario_get = AsyncMock(return_value=SimpleNamespace(id=11))
        self.limit_query = AsyncMock(return_value=[self.action_row])
        bot_action = SimpleNamespace(
            objects=SimpleNamespace(
                get=self.action_get, order_by=Mock(return_value=SimpleNamespace(limit=self.limit_query)),
            ),
            created_at=SimpleNamespace(desc=lambda: "created_at desc"),
        )
        self.guards = SimpleNamespace(check_for_action=AsyncMock(return_value=(True, "ok")))
        types_module = module_with(ActionType=self.action, BotActionStatus=Status)
        # The real policy already captured its own ActionType; only this action
        # module's late status import is isolated here.
        self.modules = {
            "app.agent.tools": self.tools,
            "app.core.permissions": self.perms,
            "app.models": module_with(
                BotAction=bot_action, Source=SimpleNamespace(objects=SimpleNamespace(get=self.source_get)),
                AgentScenario=SimpleNamespace(objects=SimpleNamespace(get=self.scenario_get)),
            ),
            "app.types": types_module,
            "app.services.social.guards": module_with(guards_checker=self.guards, extract_target_user=lambda p: None),
        }
        self.import_patch = patch.dict(sys.modules, self.modules)
        self.import_patch.start()
        self.addCleanup(self.import_patch.stop)
        self.actions = load_source("_action_tools_actual", "app/agent/toolset/actions.py")
        self.transport = self.actions._execute_action = AsyncMock(side_effect=AssertionError("No provider calls"))
        self.user.has_perm_for = (
            lambda model, action: model == "botaction" and action == self.perms._resolve_action("view")
        )

    async def preview(self, **kwargs):
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user):
            return await self.actions.action_send(action_id=9, **kwargs)

    async def test_registry_points_to_real_argument_handler_not_tier_helper(self):
        spec = self.tools.TOOL_REGISTRY["action_send"]
        self.assertIs(spec.handler, self.actions.action_send)
        self.assertIsNot(spec.handler, self.actions._auto_actions_forced_dry_run)
        self.assertEqual(spec.required_permission, "botaction.view")
        self.assertTrue(spec.confirm)
        self.assertEqual(self.tools.TOOL_REGISTRY["actions_log"].required_permission, "botaction.view")

    async def test_direct_unknown_subject_cannot_read_or_preview(self):
        with self.assertRaises(self.perms.PermissionDeniedError):
            await self.actions.action_send(action_id=9)
        with self.assertRaises(self.perms.PermissionDeniedError):
            await self.actions.actions_log()
        self.action_get.assert_not_awaited()
        self.limit_query.assert_not_awaited()

    async def test_workspace_owner_without_botaction_view_is_denied(self):
        self.user.has_perm_for = lambda *args: False
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user, is_owner=True):
            with self.assertRaises(self.perms.PermissionDeniedError):
                await self.actions.action_send(action_id=9)
        self.action_get.assert_not_awaited()

    async def test_preview_keeps_pending_and_calls_no_transport(self):
        result = await self.preview()
        self.assertTrue(result["success"])
        self.assertTrue(result["preview_only"])
        self.assertEqual(result["status"], "PENDING")
        self.assertEqual(self.action_row.status, self.status.PENDING)
        self.assertIsNone(self.action_row.confirmed_at)
        self.assertIsNone(self.action_row.confirmed_by)
        self.transport.assert_not_awaited()
        self.guards.check_for_action.assert_awaited_once()

    async def test_live_or_non_boolean_dry_run_is_refused_before_storage(self):
        for value in (False, 0, 1, "true", None):
            result = await self.preview(dry_run=value)
            self.assertEqual(result["code"], "live_action_disabled")
        self.action_get.assert_not_awaited()
        self.transport.assert_not_awaited()

    async def test_invalid_id_and_payload_are_refused_without_publication(self):
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user):
            for value in (True, -1, 0, "9", 2**31):
                result = await self.actions.action_send(action_id=value)
                self.assertEqual(result["code"], "invalid_arguments")
        self.action_get.assert_not_awaited()
        self.action_row.payload = []
        result = await self.preview()
        self.assertFalse(result["success"])
        self.transport.assert_not_awaited()

    async def test_unavailable_cross_workspace_references_do_not_preview(self):
        self.source_get.return_value = None
        result = await self.preview()
        self.assertFalse(result["success"])
        self.guards.check_for_action.assert_not_awaited()
        self.transport.assert_not_awaited()

    async def test_guard_denial_or_nonpending_action_does_not_change_state(self):
        self.guards.check_for_action.return_value = (False, "PRIVATE-guard-details")
        result = await self.preview()
        self.assertEqual(result["code"], "guards_blocked")
        self.assertNotIn("PRIVATE", str(result))
        self.assertEqual(self.action_row.status, self.status.PENDING)
        self.action_row.status = self.status.APPROVED
        result = await self.preview()
        self.assertFalse(result["success"])
        self.assertEqual(self.action_row.status, self.status.APPROVED)
        self.transport.assert_not_awaited()

    async def test_log_limit_is_bounded_and_valid_log_contract_preserved(self):
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user):
            for value in (True, -1, 0, 101, "10"):
                result = await self.actions.actions_log(limit=value)
                self.assertEqual(result["code"], "invalid_arguments")
            self.limit_query.assert_not_awaited()
            result = await self.actions.actions_log(limit=10)
        self.assertEqual(result["actions"][0]["id"], 9)
        self.limit_query.assert_awaited_once_with(10)


if __name__ == "__main__":
    unittest.main()
