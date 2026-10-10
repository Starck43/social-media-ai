"""Actual identity/runtime source with infrastructure mocked; tests NOT RUN.

Run: python tests/test_runtime_identity_authorization.py. No DB/live transports.
Pytest still uses repository conftest and its isolated DB target.
"""

import asyncio
import importlib.util
import sys
import unittest
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


HELPERS = load_source("_runtime_identity_helpers", "tests/test_identity_permissions_boundary.py")


class RuntimeIdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        HELPERS.BoundaryTests.setUp(self)
        self.user.id = 12
        self.user.role_id = 2
        self.user.role = SimpleNamespace(id=2, codename="VIEWER")
        self.membership = SimpleNamespace(
            id=41, tenant_id=31, channel="telegram", external_user_id="7", user_id=12,
            role_id=3, role=SimpleNamespace(id=3, codename="SUPERUSER"), is_active=True,
        )
        self.tenant_row = SimpleNamespace(id=31, is_active=True)
        self.channel_row = SimpleNamespace(tenant_id=31, channel="telegram", chat_id="7", is_active=True)
        self.users = SimpleNamespace(get=AsyncMock(return_value=self.user))
        self.users.prefetch_related = Mock(return_value=self.users)
        self.members = SimpleNamespace(get=AsyncMock(return_value=self.membership))
        self.members.prefetch_related = Mock(return_value=self.members)
        self.tenants = SimpleNamespace(get=AsyncMock(return_value=self.tenant_row))
        self.channels = SimpleNamespace(get=AsyncMock(return_value=self.channel_row))
        self.resolution = SimpleNamespace(
            tenant_id=31, channel="telegram", chat_id="7", user_id="7", onboarded=False,
            is_owner=True, is_platform_owner=True,
        )
        self.inbound = SimpleNamespace(channel="telegram", chat_id="7", user_id="7", text="hello")
        self.modules = {
            "app.core.tenant_context": self.tenant,
            "app.core.permissions": self.perms,
            "app.models": HELPERS.module_with(
                User=SimpleNamespace(objects=self.users), Tenant=SimpleNamespace(objects=self.tenants),
            ),
            "app.models.managers.tenant_manager": HELPERS.module_with(
                tenant_users=self.members, tenant_channels=self.channels,
                tenants=self.tenants,
            ),
        }
        self.import_patch = patch.dict(sys.modules, self.modules)
        self.import_patch.start()
        self.addCleanup(self.import_patch.stop)
        self.identity = load_source("_runtime_identity_actual", "app/agent/identity.py")

    async def resolve(self):
        with self.tenant.tenant_scope(31):
            return await self.identity.resolve_runtime_identity(self.resolution)

    def runtime(self):
        modules = {
            **self.modules,
            "app.agent.identity": self.identity,
            "app.agent.prompts": HELPERS.module_with(DEFAULT_SYSTEM_PROMPT="test", AGENT_HELP_TEXT="test-help"),
            "app.agent.tools": HELPERS.module_with(
                TOOL_REGISTRY={}, call_tool=AsyncMock(), to_openai_call=lambda call: call, tool_specs=lambda: [],
            ),
            "app.channels.base": HELPERS.module_with(Inbound=SimpleNamespace),
            "app.core.config": HELPERS.module_with(settings=SimpleNamespace(AGENT_MAX_ITERATIONS=2)),
            "app.services.tenancy.resolver": HELPERS.module_with(
                Resolution=SimpleNamespace, is_platform_owner=lambda *args: False,
                resolve_inbound=AsyncMock(), tenant_daily_cost_limit=AsyncMock(return_value=0),
            ),
        }
        with patch.dict(sys.modules, modules):
            confirmation = load_source("_runtime_identity_confirmation", "app/agent/confirmation.py")
            with patch.dict(sys.modules, {"app.agent.confirmation": confirmation}):
                return load_source("_runtime_identity_runtime", "app/agent/runtime.py")

    async def test_active_bound_identity_uses_exact_tenant_and_eager_rights(self):
        identity = await self.resolve()
        self.assertIsNotNone(identity)
        self.assertIs(identity.user, self.user)
        self.assertTrue(identity.is_owner)
        self.members.get.assert_awaited_once_with(
            tenant_id=31, channel="telegram", external_user_id="7", is_active=True,
        )
        self.users.prefetch_related.assert_called_once_with("role.permissions.model_type")
        self.users.get.assert_awaited_once_with(id=12, is_active=True)

    async def test_missing_or_wrong_tenant_context_has_no_identity_queries(self):
        self.assertIsNone(await self.identity.resolve_runtime_identity(self.resolution))
        with self.tenant.tenant_scope(32):
            self.assertIsNone(await self.identity.resolve_runtime_identity(self.resolution))
        self.tenants.get.assert_not_awaited()

    async def test_bypass_is_not_an_interactive_identity(self):
        with self.tenant.tenant_scope(31, bypass=True):
            self.assertIsNone(await self.identity.resolve_runtime_identity(self.resolution))
        self.tenants.get.assert_not_awaited()

    async def test_inactive_tenant_is_denied_before_user_load(self):
        self.tenant_row.is_active = False
        self.assertIsNone(await self.resolve())
        self.users.get.assert_not_awaited()

    async def test_missing_unbound_inactive_membership_is_denied(self):
        self.members.get.return_value = None
        self.assertIsNone(await self.resolve())
        self.members.get.return_value = self.membership
        self.membership.user_id = None
        self.assertIsNone(await self.resolve())
        self.membership.user_id = 12
        self.membership.is_active = False
        self.assertIsNone(await self.resolve())
        self.users.get.assert_not_awaited()

    async def test_cross_tenant_or_wrong_channel_membership_is_denied(self):
        self.membership.tenant_id = 32
        self.assertIsNone(await self.resolve())
        self.membership.tenant_id = 31
        self.membership.channel = "max"
        self.assertIsNone(await self.resolve())
        self.users.get.assert_not_awaited()

    async def test_revoked_or_wrong_chat_binding_is_denied(self):
        self.channel_row.is_active = False
        self.assertIsNone(await self.resolve())
        self.channel_row.is_active = True
        self.channel_row.chat_id = "other"
        self.assertIsNone(await self.resolve())
        self.users.get.assert_not_awaited()

    async def test_missing_inactive_or_rebound_user_is_denied(self):
        self.users.get.return_value = None
        self.assertIsNone(await self.resolve())
        self.users.get.return_value = self.user
        self.user.is_active = False
        self.assertIsNone(await self.resolve())
        self.user.is_active = True
        self.user.id = 13
        self.assertIsNone(await self.resolve())

    async def test_missing_or_inconsistent_platform_role_is_denied(self):
        self.user.role = None
        self.assertIsNone(await self.resolve())
        self.user.role = SimpleNamespace(id=99, codename="VIEWER")
        self.assertIsNone(await self.resolve())
        self.user.role = SimpleNamespace(id=2, codename="VIEWER")
        self.user.role_id = None
        self.assertIsNone(await self.resolve())

    async def test_web_identity_requires_matching_user_membership_and_chat(self):
        self.resolution.channel = "web"
        self.resolution.user_id = self.resolution.chat_id = "12"
        self.membership.channel = "web"
        self.membership.external_user_id = "12"
        self.assertIsNotNone(await self.resolve())
        self.channels.get.assert_not_awaited()
        self.membership.user_id = 13
        self.assertIsNone(await self.resolve())

    async def test_null_membership_role_and_env_owner_flag_do_not_elevate(self):
        self.membership.role_id = None
        self.membership.role = None
        self.assertFalse((await self.resolve()).is_owner)

    async def test_refresh_reloads_user_and_rejects_identity_rebinding(self):
        original = await self.resolve()
        replacement = SimpleNamespace(**vars(self.user))
        self.users.get.return_value = replacement
        with self.tenant.tenant_scope(31):
            fresh = await self.identity.refresh_runtime_identity(original)
        self.assertIs(fresh.user, replacement)
        self.membership.id = 99
        with self.tenant.tenant_scope(31):
            self.assertIsNone(await self.identity.refresh_runtime_identity(original))

    async def test_lookup_failure_is_closed_and_logs_no_private_error(self):
        self.users.get.side_effect = RuntimeError("PRIVATE-token")
        with patch.object(self.identity.logger, "warning") as warning:
            self.assertIsNone(await self.resolve())
        warning.assert_called_once_with("runtime_identity_load_failed")

    async def test_admission_denial_does_not_start_turn_or_llm(self):
        runtime = self.runtime()
        runtime._handle_authorized_turn = AsyncMock()
        self.members.get.return_value = None
        with self.tenant.tenant_scope(31):
            reply = await runtime._handle_in_tenant(self.inbound, self.resolution)
        self.assertIn("активным пользователем", reply)
        runtime._handle_authorized_turn.assert_not_awaited()

    async def test_permission_scope_covers_awaited_turn_and_restores(self):
        runtime = self.runtime()

        async def turn(inbound, resolution, identity):
            await asyncio.sleep(0)
            self.assertIs(self.perms.get_current_user(), self.user)
            self.assertTrue(self.perms.has_permission(self.user, "agenttask", "create"))
            return "ok"

        runtime._handle_authorized_turn = turn
        with self.tenant.tenant_scope(31):
            self.assertEqual(await runtime._handle_in_tenant(self.inbound, self.resolution), "ok")
        self.assertIsNone(self.perms.get_current_user())

    async def test_scope_restores_when_admitted_turn_raises(self):
        runtime = self.runtime()
        runtime._handle_authorized_turn = AsyncMock(side_effect=RuntimeError("test-only"))
        with self.tenant.tenant_scope(31), self.assertRaises(RuntimeError):
            await runtime._handle_in_tenant(self.inbound, self.resolution)
        self.assertIsNone(self.perms.get_current_user())

    async def test_mismatched_inbound_resolution_does_not_load_identity(self):
        runtime = self.runtime()
        self.inbound.user_id = "another"
        with self.tenant.tenant_scope(31):
            await runtime._handle_in_tenant(self.inbound, self.resolution)
        self.tenants.get.assert_not_awaited()

    async def test_session_match_requires_active_same_tenant_channel_chat(self):
        identity = await self.resolve()
        session = SimpleNamespace(id=5, tenant_id=31, channel="telegram", chat_id="7", is_active=True)
        self.assertTrue(self.identity.session_matches_identity(session, identity))
        for field, value in (("is_active", False), ("tenant_id", 32), ("channel", "max"), ("chat_id", "8")):
            wrong = SimpleNamespace(**vars(session))
            setattr(wrong, field, value)
            self.assertFalse(self.identity.session_matches_identity(wrong, identity))


if __name__ == "__main__":
    unittest.main()
