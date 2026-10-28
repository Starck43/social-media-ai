"""Source-isolated P11-T09 checks: actual policy, contracts and one-use approval.

Run with Python directly (unittest): no root DB conftest, app bootstrap, sockets,
paid providers, migrations or runtime startup. Toolset declaration coverage uses
AST bodies replaced by inert stubs; this is NOT application/DB acceptance.
"""

from __future__ import annotations

import ast
import asyncio
import json
import sys
import unittest
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, patch

import test_identity_permissions_boundary as boundary

load_source = boundary.load_source
module_with = boundary.module_with


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        boundary.BoundaryTests.setUp(self)
        self.contracts = load_source("_p11_t09_contracts", "app/agent/tool_contracts.py")
        self.wire = load_source("_p11_t09_wire", "app/services/llm_tool_serialization.py")
        modules = {
            "app.core.permissions": self.perms,
            "app.core.tenant_context": self.tenant,
            "app.agent": module_with(toolset=module_with()),
            "app.agent.tool_contracts": self.contracts,
            "app.services.llm_tool_serialization": self.wire,
        }
        with patch.dict(sys.modules, modules):
            self.identity = load_source("_p11_t09_identity", "app/agent/identity.py")
            modules["app.agent.identity"] = self.identity
            with patch.dict(sys.modules, modules):
                self.confirmation = load_source("_p11_t09_confirmation", "app/agent/confirmation.py")
                modules["app.agent.confirmation"] = self.confirmation
                with patch.dict(sys.modules, modules):
                    self.tools = load_source("_p11_t09_tools", "app/agent/tools.py")
        identity_patch = patch.dict(sys.modules, {"app.agent.identity": self.identity})
        identity_patch.start()
        self.addCleanup(identity_patch.stop)
        self.handler = AsyncMock(return_value={"ok": True})
        self.spec_args = {
            "name": "test_write",
            "description": "Test tool",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "integer", "minimum": 1}},
                "required": ["value"],
            },
            "required_permission": "collection.source.update",
        }

    def register(self, *, confirm=False, **overrides):
        async def handler(value: int) -> dict[str, Any]:
            return await self.handler(value=value)

        self.tools.tool(**(self.spec_args | overrides), confirm=confirm)(handler)
        return self.tools.get_tool((self.spec_args | overrides)["name"])

    def actor(self):
        return self.identity.RuntimeIdentity(
            tenant_id=31,
            user_id=1,
            membership_id=5,
            channel="test",
            chat_id="chat",
            external_user_id="external",
            membership_role_id=4,
            user_role_id=None,
            is_owner=True,
            user=self.user,
            ai_chat_enabled=True,
        )

    def test_missing_unknown_and_public_policies_rejected(self):
        for permission in (
            None,
            "",
            "PUBLIC",
            "unknown.tool.view",
            "collection.source.unknown",
            "social.source.update",
        ):
            with self.subTest(permission=permission), self.assertRaises(ValueError):
                self.register(required_permission=permission)
        self.assertEqual(self.tools.all_tools(), [])

    def test_names_duplicates_schema_and_handler_validation(self):
        for name in ("", "bad tool", "a" * 65, "x.y"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.register(name=name)
        for schema in (
            {},
            {"type": "object", "properties": {}, "required": ["missing"]},
            {"type": "object", "properties": {"value": {"type": "imaginary"}}},
            {"type": "object", "properties": {"value": {"type": "integer", "maximum": "2"}}},
            {"type": "object", "properties": {"value": {"type": "integer", "enum": [True]}}},
            {"type": "object", "properties": {"value": {"type": "integer", "unreviewed": True}}},
            {"type": "object", "properties": {"other": {"type": "integer"}}},
            {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
        ):
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                self.register(parameters=schema)
        self.register()
        with self.assertRaises(ValueError):
            self.register()

        def sync(value: int) -> dict:
            return {}

        with self.assertRaises(ValueError):
            self.tools.tool(**(self.spec_args | {"name": "sync"}))(sync)

    def test_registry_and_contract_snapshots_are_read_only(self):
        spec = self.register()
        with self.assertRaises(TypeError):
            self.tools.TOOL_REGISTRY["injected"] = spec
        with self.assertRaises(FrozenInstanceError):
            spec.required_permission = None
        self.spec_args["parameters"]["properties"]["value"]["type"] = "string"
        schema = spec.parameters
        schema["properties"].clear()
        self.assertEqual(spec.parameters["properties"]["value"]["type"], "integer")
        wire = spec.to_openai()
        wire["function"]["parameters"]["properties"].clear()
        self.assertIn("value", spec.parameters["properties"])

    async def test_invalid_input_never_invokes_handler_in_either_path(self):
        self.register()
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user, is_owner=True):
            for args in (
                None,
                [],
                {},
                {"value": True},
                {"value": "2"},
                {"value": 0},
                {"value": 1, "secret": "PRIVATE"},
                {"value": float("nan")},
            ):
                with self.subTest(args=args):
                    with self.assertRaises(self.contracts.ToolInputError):
                        await self.tools.call_tool("test_write", args)
                    result = json.loads(await self.tools.execute("test_write", args))
                    self.assertEqual(result["code"], "invalid_arguments")
                    self.assertNotIn("PRIVATE", str(result))
        self.handler.assert_not_awaited()

    async def test_valid_dispatch_and_wire_compatibility(self):
        spec = self.register()
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user, is_owner=True):
            self.assertEqual(await self.tools.call_tool(spec.name, {"value": 2}), {"ok": True})
            self.assertEqual(json.loads(await self.tools.execute(spec.name, {"value": 2})), {"ok": True})
        self.assertEqual(self.tools.tool_specs(), [self.wire.tool_schema(spec)])
        self.assertEqual(
            self.tools.to_openai_call({"id": "x", "name": spec.name, "arguments": {"value": 2}})["function"][
                "arguments"
            ],
            '{"value": 2}',
        )

    async def test_direct_confirmed_denials_and_revoked_rights(self):
        spec = self.register(confirm=True)
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user):
            with self.assertRaises(self.perms.PermissionDeniedError):
                await self.tools.call_tool(spec.name, {"value": 2})
            self.assertEqual(json.loads(await self.tools.execute(spec.name, {"value": 2}))["code"], "permission_denied")
        actor = self.actor()
        with (
            self.tenant.tenant_scope(31),
            self.perms.permission_scope(self.user, is_owner=True),
            self.identity.memory_actor_scope(1, True),
        ):
            with self.assertRaises(self.tools.ToolConfirmationRequiredError):
                await self.tools.call_tool(spec.name, {"value": 2})
            with self.confirmation.confirmed_dispatch_scope(actor, spec, {"value": 2}):
                with self.perms.permission_scope(self.user):
                    with self.assertRaises(self.perms.PermissionDeniedError):
                        await self.tools.call_tool(spec.name, {"value": 2})
                    self.assertEqual(
                        json.loads(await self.tools.execute(spec.name, {"value": 2}))["code"], "permission_denied"
                    )
        self.handler.assert_not_awaited()

    async def test_confirmation_is_argument_actor_tenant_bound_and_one_use(self):
        spec = self.register(confirm=True)
        actor = self.actor()
        with (
            self.tenant.tenant_scope(31),
            self.perms.permission_scope(self.user, is_owner=True),
            self.identity.memory_actor_scope(1, True),
        ):
            with self.confirmation.confirmed_dispatch_scope(actor, spec, {"value": 2}):
                with self.assertRaises(self.tools.ToolConfirmationRequiredError):
                    await self.tools.call_tool(spec.name, {"value": 3})
                with self.tenant.tenant_scope(32):
                    self.assertEqual(
                        json.loads(await self.tools.execute(spec.name, {"value": 2}))["code"], "permission_denied"
                    )
                with self.identity.memory_actor_scope(2, True):
                    with self.assertRaises(self.tools.ToolConfirmationRequiredError):
                        await self.tools.call_tool(spec.name, {"value": 2})
                await self.tools.call_tool(spec.name, {"value": 2})
                self.assertEqual(
                    json.loads(await self.tools.execute(spec.name, {"value": 2}))["code"], "confirmation_required"
                )
        self.handler.assert_awaited_once_with(value=2)

    async def test_budgets_results_and_redacted_metadata(self):
        budget = self.contracts.ToolBudget(max_argument_bytes=20, max_result_bytes=20, timeout_seconds=0.01)
        spec = self.register(budget=budget)
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user, is_owner=True):
            self.handler.return_value = {"private": "PRIVATE" * 10}
            with patch.object(self.tools.logger, "info") as log:
                self.assertEqual(
                    json.loads(await self.tools.execute(spec.name, {"value": 1}))["code"], "tool_contract_error"
                )
                self.assertNotIn("PRIVATE", str(log.call_args_list))
            self.handler.return_value = ["wrong result type"]
            with self.assertRaises(self.contracts.ToolResultError):
                await self.tools.call_tool(spec.name, {"value": 1})

            async def slow(**kwargs):
                await asyncio.sleep(0.1)

            self.handler.side_effect = slow
            self.assertEqual(
                json.loads(await self.tools.execute(spec.name, {"value": 1}))["code"], "tool_contract_error"
            )
            self.handler.side_effect = RuntimeError("PRIVATE")
            self.assertNotIn("PRIVATE", await self.tools.execute(spec.name, {"value": 1}))
        with self.assertRaises(self.contracts.ToolInputError):
            spec.contract.arguments({"value": 1234567890123456789012345}, budget)

    def test_all_real_declarations_register_without_app_or_db_bootstrap(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        launch = load_source("_p11_t09_launch", "app/services/task_contracts.py")
        namespace = {
            "__name__": "_p11_t09_declarations",
            "_UNSET": object(),
            "tool": self.tools.tool,
            "Any": Any,
            "Optional": Optional,
            "TaskLaunchArguments": launch.TaskLaunchArguments,
            "require_permission": self.perms.require_permission,
            "_get_job_types": lambda: ("collect", "analyze", "digest", "prune", "learn", "reflect"),
        }
        # Annotation resolution needs the same module namespace, not a fake annotation result.
        module = module_with(**namespace)
        sys.modules[namespace["__name__"]] = module
        self.addCleanup(sys.modules.pop, namespace["__name__"], None)
        for path in sorted((root / "app/agent/toolset").glob("*.py")):
            tree = ast.parse(path.read_text())
            nodes = []
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    try:
                        ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        continue
                    nodes.append(node)
                if isinstance(node, ast.AsyncFunctionDef) and any(
                    isinstance(d, ast.Call) and getattr(d.func, "id", "") == "tool" for d in node.decorator_list
                ):
                    node.body = [ast.Return(ast.Constant(None))]
                    nodes.append(node)
            exec(
                compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(path), "exec"),
                module.__dict__,
            )
        self.assertGreaterEqual(len(self.tools.all_tools()), 40)
        self.assertTrue(all(t.required_permission for t in self.tools.all_tools()))


if __name__ == "__main__":
    unittest.main()
