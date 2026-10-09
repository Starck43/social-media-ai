"""Actor-bound confirmation and actual runtime/dispatch regressions; NOT RUN.

Run: python tests/test_runtime_confirmation_authorization.py; no DB/live sends.
"""

import asyncio
import copy
import importlib.util
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
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


HELPERS = load_source("_confirmation_identity_helpers", "tests/test_runtime_identity_authorization.py")


class ConfirmationAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        HELPERS.RuntimeIdentityTests.setUp(self)
        with patch.dict(sys.modules, {"app.agent.identity": self.identity}):
            self.confirmation = load_source("_confirmation_actual", "app/agent/confirmation.py")
        self.actor = self.identity.RuntimeIdentity(
            tenant_id=31, user_id=12, membership_id=41, channel="telegram", chat_id="7",
            external_user_id="7", membership_role_id=3, user_role_id=2, is_owner=True, user=self.user,
        )
        self.spec = SimpleNamespace(name="test_write", confirm=True, required_permission="source.update", parameters={})
        self.session = SimpleNamespace(
            id=51, tenant_id=31, channel="telegram", chat_id="7", is_active=True, state={},
            save_state=AsyncMock(), append=AsyncMock(), touch=AsyncMock(),
        )

        async def save_state(state):
            self.session.state = state

        self.session.save_state.side_effect = save_state
        self.now = datetime.now(timezone.utc)
        self.pending = self.confirmation.make_pending_intent(
            self.actor, self.session, self.spec, {"source_id": 9}, "c1", now=self.now,
        )

    def runtime(self):
        runtime = HELPERS.RuntimeIdentityTests.runtime(self)
        runtime.TOOL_REGISTRY = {"test_write": self.spec}
        runtime._cost_today = AsyncMock(return_value=0)
        runtime._build_messages = AsyncMock(return_value=[])
        runtime._write_tool_result = AsyncMock()
        runtime._human_confirmation = lambda name, args: "Подтвердите действие"
        return runtime

    def tools(self):
        with patch.dict(sys.modules, {
            "app.core.permissions": self.perms,
            "app.agent.confirmation": self.confirmation,
            "app.agent": HELPERS.HELPERS.module_with(toolset=HELPERS.HELPERS.module_with()),
        }):
            tools = load_source("_confirmation_tools_actual", "app/agent/tools.py")
        self.handler = AsyncMock(return_value={"ok": True})
        self.spec.handler = self.handler
        tools.TOOL_REGISTRY["test_write"] = self.spec
        return tools

    async def authorized_turn(self, runtime, pending, text="да"):
        self.session.state = {"pending_confirmation": pending}
        self.inbound.text = text
        manager = SimpleNamespace(get_or_create=AsyncMock(return_value=self.session))
        with patch.dict(sys.modules, {
            "app.models.managers.agent_session_manager": HELPERS.HELPERS.module_with(agent_sessions=manager),
        }), self.tenant.tenant_scope(31), self.perms.permission_scope(self.user, is_owner=True):
            return await runtime._handle_authorized_turn(self.inbound, self.resolution, self.actor)

    async def test_valid_intent_and_immutable_arguments_copy(self):
        self.assertIsNone(self.confirmation.pending_rejection(self.pending, self.actor, self.session, self.spec))
        args = {"source_id": 9, "nested": {"value": "initial"}}
        intent = self.confirmation.make_pending_intent(self.actor, self.session, self.spec, args, "c1")
        args["nested"]["value"] = "changed"
        self.assertEqual(intent["args"]["nested"]["value"], "initial")

    async def test_legacy_actorless_intent_is_denied(self):
        legacy = {k: v for k, v in self.pending.items() if k != "authorization"}
        self.assertEqual(
            self.confirmation.pending_rejection(legacy, self.actor, self.session, self.spec),
            "unbound_intent",
        )

    async def test_other_actor_tenant_session_and_role_changes_are_denied(self):
        for field, value, expected in (
            ("user_id", 13, "actor_mismatch"), ("tenant_id", 32, "actor_mismatch"),
            ("membership_role_id", 4, "authority_changed"), ("user_role_id", 4, "authority_changed"),
        ):
            fields = dict(vars(self.actor))
            fields[field] = value
            other = self.identity.RuntimeIdentity(**fields)
            self.assertEqual(
                self.confirmation.pending_rejection(self.pending, other, self.session, self.spec),
                expected,
            )
        wrong_session = SimpleNamespace(**vars(self.session))
        wrong_session.id = 52
        self.assertEqual(
            self.confirmation.pending_rejection(self.pending, self.actor, wrong_session, self.spec),
            "session_mismatch",
        )

    async def test_expired_naive_and_malformed_expiry_are_denied(self):
        for expiry in (
            (self.now - timedelta(seconds=1)).isoformat(),
            self.now.replace(tzinfo=None).isoformat(), "bad", None,
        ):
            changed = {**self.pending, "expires_at": expiry}
            self.assertEqual(
                self.confirmation.pending_rejection(changed, self.actor, self.session, self.spec),
                "expired_intent",
            )

    async def test_argument_and_registry_contract_changes_are_denied(self):
        changed = {**self.pending, "args": {"source_id": 10}}
        self.assertEqual(
            self.confirmation.pending_rejection(changed, self.actor, self.session, self.spec),
            "arguments_changed",
        )
        self.spec.required_permission = "source.delete"
        self.assertEqual(
            self.confirmation.pending_rejection(self.pending, self.actor, self.session, self.spec),
            "tool_changed",
        )
        self.spec.required_permission = "source.update"
        self.spec.parameters = {"required": ["new"]}
        self.assertEqual(
            self.confirmation.pending_rejection(self.pending, self.actor, self.session, self.spec),
            "tool_changed",
        )

    async def test_malformed_stage_requests_cannot_create_intent(self):
        for args, call_id in (([], "c1"), ({1: "bad"}, "c1"), ({"nan": float("nan")}, "c1"), ({}, None)):
            self.assertIsNone(self.confirmation.make_pending_intent(self.actor, self.session, self.spec, args, call_id))
        self.spec.required_permission = None
        self.assertIsNone(self.confirmation.make_pending_intent(self.actor, self.session, self.spec, {}, "c1"))

    async def test_direct_dispatch_without_approval_is_denied_in_both_paths(self):
        tools = self.tools()
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user, is_owner=True):
            with self.assertRaises(tools.ToolConfirmationRequiredError):
                await tools.call_tool("test_write", {"source_id": 9})
            result = json.loads(await tools.execute("test_write", {"source_id": 9}))
        self.assertEqual(result["code"], "confirmation_required")
        self.handler.assert_not_awaited()

    async def test_exact_grant_is_single_use_and_does_not_allow_different_arguments(self):
        tools = self.tools()
        args = {"source_id": 9}
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user, is_owner=True):
            with self.confirmation.confirmed_dispatch_scope(self.actor, self.spec, args):
                with self.assertRaises(tools.ToolConfirmationRequiredError):
                    await tools.call_tool("test_write", {"source_id": 10})
                self.assertEqual(await tools.call_tool("test_write", args), {"ok": True})
                with self.assertRaises(tools.ToolConfirmationRequiredError):
                    await tools.call_tool("test_write", args)
        self.handler.assert_awaited_once_with(source_id=9)

    async def test_inherited_child_task_cannot_reuse_consumed_approval(self):
        tools = self.tools()
        args = {"source_id": 9}
        ready = asyncio.Event()

        async def later_call():
            await ready.wait()
            with self.assertRaises(tools.ToolConfirmationRequiredError):
                await tools.call_tool("test_write", args)

        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user, is_owner=True):
            with self.confirmation.confirmed_dispatch_scope(self.actor, self.spec, args):
                child = asyncio.create_task(later_call())
                try:
                    await tools.call_tool("test_write", args)
                finally:
                    ready.set()
                await child
        self.handler.assert_awaited_once_with(source_id=9)

    async def test_approval_does_not_override_denied_rights(self):
        tools = self.tools()
        self.spec.required_permission = "llmmodel.create"
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user, is_owner=True):
            with self.confirmation.confirmed_dispatch_scope(self.actor, self.spec, {}):
                with self.assertRaises(self.perms.PermissionDeniedError):
                    await tools.call_tool("test_write", {})
        self.handler.assert_not_awaited()

    async def test_revoked_identity_before_dispatch_invokes_no_handler(self):
        runtime = self.runtime()
        runtime.call_tool = AsyncMock()
        runtime.refresh_runtime_identity = AsyncMock(return_value=None)
        with self.tenant.tenant_scope(31):
            output, stop = await runtime._dispatch_or_stage(self.session, "test_write", {}, "c1", self.actor)
        self.assertTrue(stop)
        runtime.call_tool.assert_not_awaited()
        self.session.save_state.assert_not_awaited()

    async def test_confirmation_stages_and_never_invokes_handler_on_first_turn(self):
        runtime = self.runtime()
        runtime.call_tool = AsyncMock()
        runtime.refresh_runtime_identity = AsyncMock(return_value=self.actor)
        with self.tenant.tenant_scope(31):
            output, stop = await runtime._dispatch_or_stage(
                self.session, "test_write", {"source_id": 9}, "c1", self.actor,
            )
        self.assertTrue(stop)
        self.assertEqual(output, "Подтвердите действие")
        self.assertEqual(
            self.session.state["pending_confirmation"]["authorization"]["actor"],
            self.actor.actor_binding(),
        )
        runtime.call_tool.assert_not_awaited()

    async def test_confirm_valid_intent_refreshes_after_storage_and_resumes(self):
        runtime = self.runtime()
        tools = self.tools()
        runtime.call_tool = tools.call_tool
        # Runtime's grant module and tools' grant module must be the SAME instance.
        runtime.confirmed_dispatch_scope = self.confirmation.confirmed_dispatch_scope
        runtime.refresh_runtime_identity = AsyncMock(return_value=self.actor)
        runtime._run_tool_loop = AsyncMock(return_value="continued")
        result = await self.authorized_turn(runtime, self.pending)
        self.assertEqual(result, "continued")
        self.assertEqual(runtime.refresh_runtime_identity.await_count, 3)
        self.handler.assert_awaited_once_with(source_id=9)
        self.assertNotIn("pending_confirmation", self.session.state)

    async def test_revoked_rights_or_identity_on_yes_never_invoke_handler(self):
        for revoked in ("rights", "identity"):
            runtime = self.runtime()
            runtime.call_tool = AsyncMock()
            fields = dict(vars(self.actor))
            fields["is_owner"] = False
            fresh = self.identity.RuntimeIdentity(**fields)
            runtime.refresh_runtime_identity = AsyncMock(return_value=None if revoked == "identity" else fresh)
            result = await self.authorized_turn(runtime, copy.deepcopy(self.pending))
            self.assertIn("отклонено", result)
            runtime.call_tool.assert_not_awaited()

    async def test_other_actor_yes_preserves_original_pending(self):
        runtime = self.runtime()
        runtime.call_tool = AsyncMock()
        wrong = copy.deepcopy(self.pending)
        wrong["authorization"]["actor"]["user_id"] = 99
        runtime.refresh_runtime_identity = AsyncMock(return_value=self.actor)
        result = await self.authorized_turn(runtime, wrong)
        self.assertIn("отклонено", result)
        self.assertIn("pending_confirmation", self.session.state)
        runtime.call_tool.assert_not_awaited()

    async def test_other_actor_cannot_cancel_or_stop_pending_intent(self):
        for text in ("нет", "/stop"):
            runtime = self.runtime()
            runtime.call_tool = AsyncMock()
            runtime.refresh_runtime_identity = AsyncMock(return_value=self.actor)
            wrong = copy.deepcopy(self.pending)
            wrong["authorization"]["actor"]["user_id"] = 99
            result = await self.authorized_turn(runtime, wrong, text=text)
            self.assertIn("другому пользователю", result)
            self.assertIn("pending_confirmation", self.session.state)
            self.session.save_state.assert_not_awaited()
            runtime.call_tool.assert_not_awaited()

    async def test_memory_command_uses_fresh_membership_owner_not_resolution_flag(self):
        runtime = self.runtime()
        fields = dict(vars(self.actor))
        fields["is_owner"] = False
        fresh = self.identity.RuntimeIdentity(**fields)
        runtime.refresh_runtime_identity = AsyncMock(return_value=fresh)
        result = await self.authorized_turn(runtime, self.pending, text="/memory clear")
        self.assertIn("только владелец", result)
        self.assertIn("pending_confirmation", self.session.state)

    async def test_second_identity_refresh_after_consumption_can_refuse(self):
        runtime = self.runtime()
        runtime.call_tool = AsyncMock()
        runtime.refresh_runtime_identity = AsyncMock(side_effect=[self.actor, self.actor, None])
        result = await self.authorized_turn(runtime, self.pending)
        self.assertIn("отклонено", result)
        self.assertNotIn("pending_confirmation", self.session.state)
        runtime.call_tool.assert_not_awaited()

    async def test_batch_stops_effects_after_first_stage_but_records_every_tool_result(self):
        runtime = self.runtime()
        runtime.refresh_runtime_identity = AsyncMock(return_value=self.actor)
        runtime._record_usage = AsyncMock()
        runtime._chat = AsyncMock(return_value={"content": "", "usage": {}, "tool_calls": [
            {"id": "c1", "name": "test_write", "arguments": {"source_id": 9}},
            {"id": "c2", "name": "test_write", "arguments": {"source_id": 10}},
        ]})
        runtime.call_tool = AsyncMock()
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user, is_owner=True):
            reply = await runtime._run_tool_loop(self.session, [], [], 0, self.actor)
        self.assertIn("Подтвердите", reply)
        self.assertEqual(self.session.state["pending_confirmation"]["args"], {"source_id": 9})
        results = [c for c in self.session.append.await_args_list if c.args and c.args[0] == "tool"]
        self.assertEqual([c.kwargs["tool_call_id"] for c in results], ["c1", "c2"])
        runtime.call_tool.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
