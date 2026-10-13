"""Manual digest send rights and confirmation, without DB/providers/transports.

Reuse the actual-source boundary loader; only infrastructure is stubbed.
Owner-local integration checks remain separate from this isolated regression.
"""

import ast
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]


def load_source(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


HELPERS = load_source("_digest_dispatch_helpers", "tests/test_tool_dispatch_permissions.py")


class DigestSendPermissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        HELPERS.DispatchPermissionTests.setUp(self)
        self.user.id = 12
        self.user.role_id = 2
        self.actor = self.confirmation.RuntimeIdentity(
            tenant_id=31,
            user_id=12,
            membership_id=41,
            channel="telegram",
            chat_id="7",
            external_user_id="7",
            membership_role_id=3,
            user_role_id=2,
            is_owner=False,
            user=self.user,
        )
        self.session = SimpleNamespace(id=51, tenant_id=31, channel="telegram", chat_id="7", is_active=True)
        with patch.dict(
            sys.modules,
            {
                "app.agent.tools": self.tools,
                "app.services.ai.reporting": HELPERS.HELPERS.module_with(ReportAggregator=object),
            },
        ):
            load_source("_digest_reports_source", "app/agent/toolset/reports.py")
        self.spec = self.tools.get_tool("digest_send_now")
        self.publisher = AsyncMock(return_value={"status": "sent"})
        builder_patch = patch.dict(
            sys.modules,
            {
                "app.services.digest.builder": HELPERS.HELPERS.module_with(build_and_publish=self.publisher),
            },
        )
        builder_patch.start()
        self.addCleanup(builder_patch.stop)

    def grant(self, action):
        self.user.has_perm_for = lambda model, right: model == "digestrun" and right == action

    async def assert_denied(self):
        with self.assertRaises(self.perms.PermissionDeniedError):
            await self.tools.call_tool("digest_send_now", {})
        result = json.loads(await self.tools.execute("digest_send_now", {}))
        self.assertEqual(result["code"], "permission_denied")
        self.publisher.assert_not_awaited()

    def test_agent_and_web_use_create_and_preview_stays_view(self):
        self.assertEqual(self.spec.required_permission, "digestrun.create")
        self.assertTrue(self.spec.confirm)
        self.assertEqual(self.tools.get_tool("report_period").required_permission, "digestrun.view")
        self.assertFalse(self.tools.get_tool("report_period").confirm)
        tree = ast.parse((ROOT / "app/web/digests.py").read_text())
        handler = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "digest_send_now")
        guards = [
            n
            for n in ast.walk(handler)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "guard_web"
        ]
        self.assertEqual(len(guards), 1)
        self.assertEqual([ast.literal_eval(arg) for arg in guards[0].args[1:3]], ["digestrun", "create"])

    async def test_view_only_cannot_publish_even_with_confirmation(self):
        self.grant(self.action.VIEW)
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user):
            with self.confirmation.confirmed_dispatch_scope(self.actor, self.spec, {}):
                await self.assert_denied()

    async def test_update_only_cannot_publish_even_with_confirmation(self):
        self.grant(self.action.UPDATE)
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user):
            with self.confirmation.confirmed_dispatch_scope(self.actor, self.spec, {}):
                await self.assert_denied()

    async def test_create_still_requires_confirmation_on_both_dispatch_paths(self):
        self.grant(self.action.CREATE)
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user):
            with self.assertRaises(self.tools.ToolConfirmationRequiredError):
                await self.tools.call_tool("digest_send_now", {})
            result = json.loads(await self.tools.execute("digest_send_now", {}))
        self.assertEqual(result["code"], "confirmation_required")
        self.publisher.assert_not_awaited()

    async def test_confirmed_create_dispatches_same_arguments_on_both_paths(self):
        self.grant(self.action.CREATE)
        args = {"period": "week", "group_by": "sources", "time_breakdown": True}
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user):
            for encoded in (False, True):
                with self.confirmation.confirmed_dispatch_scope(self.actor, self.spec, args):
                    result = await (self.tools.execute if encoded else self.tools.call_tool)("digest_send_now", args)
                self.assertEqual(json.loads(result) if encoded else result, {"status": "sent"})
        self.assertEqual(self.publisher.await_count, 2)
        self.publisher.assert_awaited_with(**args)

    async def test_revoked_create_denies_previously_confirmed_send(self):
        self.grant(self.action.CREATE)
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user):
            with self.confirmation.confirmed_dispatch_scope(self.actor, self.spec, {}):
                self.grant(self.action.VIEW)
                await self.assert_denied()

    async def test_inactive_actor_denied_even_with_create_and_confirmation(self):
        self.grant(self.action.CREATE)
        self.user.is_active = False
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user):
            with self.confirmation.confirmed_dispatch_scope(self.actor, self.spec, {}):
                await self.assert_denied()

    async def test_missing_identity_denied(self):
        with self.tenant.tenant_scope(31), self.perms.permission_scope(None):
            await self.assert_denied()

    async def test_confirmation_is_single_use(self):
        self.grant(self.action.CREATE)
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user):
            with self.confirmation.confirmed_dispatch_scope(self.actor, self.spec, {}):
                await self.tools.call_tool("digest_send_now", {})
                result = json.loads(await self.tools.execute("digest_send_now", {}))
        self.assertEqual(result["code"], "confirmation_required")
        self.publisher.assert_awaited_once_with(period="day", group_by="themes", time_breakdown=False)

    def test_old_update_intent_is_invalid_after_permission_change(self):
        # The contract digest includes the right, so old pending approvals must be requested again.
        self.spec.required_permission = "digestrun.update"
        pending = self.confirmation.make_pending_intent(self.actor, self.session, self.spec, {}, "c1")
        self.assertIsNotNone(pending)
        self.spec.required_permission = "digestrun.create"
        self.assertEqual(
            self.confirmation.pending_rejection(pending, self.actor, self.session, self.spec), "tool_changed"
        )


if __name__ == "__main__":
    unittest.main()
