"""Source-isolated P11-T09 checks: actual policy, contracts and one-use approval.

Run with Python directly (unittest): no root DB conftest, app bootstrap, sockets,
paid providers, migrations or runtime startup. Declaration coverage includes AST
stubs and real toolset lazy imports with infrastructure doubles/network traps;
this is NOT application/DB acceptance.
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
        for attribute, replacement in (
            ("_input_model", object()),
            ("_result_adapter", object()),
            ("input_model", object()),
            ("result_adapter", object()),
            ("arguments", lambda *_args: {}),
            ("result", lambda *_args: None),
        ):
            with self.subTest(attribute=attribute), self.assertRaises(FrozenInstanceError):
                setattr(spec.contract, attribute, replacement)
        self.assertEqual(spec.contract.arguments({"value": 2}, self.contracts.ToolBudget()), {"value": 2})
        self.assertEqual(spec.contract.result({"ok": True}, self.contracts.ToolBudget()), {"ok": True})
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
                {"value": 1, "unexpected": "PRIVATE"},
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

    async def test_nested_non_json_values_never_reach_handler(self):
        async def payload(value: dict[str, Any]) -> dict[str, Any]:
            return await self.handler(value=value)

        self.tools.tool(
            name="payload",
            description="Nested JSON boundary",
            parameters={"type": "object", "properties": {"value": {"type": "object"}}, "required": ["value"]},
            required_permission="collection.source.update",
        )(payload)
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user, is_owner=True):
            for value in ({"nested": {1: "coerced key"}}, {"nested": (1, 2)}, {"nested": float("nan")}):
                with self.subTest(value=value):
                    with self.assertRaises(self.contracts.ToolInputError):
                        await self.tools.call_tool("payload", {"value": value})
                    self.assertEqual(
                        json.loads(await self.tools.execute("payload", {"value": value}))["code"], "invalid_arguments"
                    )
        self.handler.assert_not_awaited()

    async def test_nested_non_json_results_are_rejected(self):
        spec = self.register()
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user, is_owner=True):
            for value in ({"nested": {1: "coerced key"}}, {"nested": (1, 2)}):
                self.handler.return_value = value
                with self.subTest(value=value):
                    with self.assertRaises(self.contracts.ToolResultError):
                        await self.tools.call_tool(spec.name, {"value": 1})
                    self.assertEqual(
                        json.loads(await self.tools.execute(spec.name, {"value": 1}))["code"], "tool_contract_error"
                    )

    def test_real_toolset_lazy_import_and_cache_with_infrastructure_doubles(self):
        import importlib
        from pathlib import Path
        from types import ModuleType

        root = Path(__file__).resolve().parents[2]

        def package(name, path):
            module = ModuleType(name)
            module.__path__ = [str(path)]
            return module

        class Model:
            pass

        tree = ast.parse((root / "app/jobs/handlers.py").read_text())
        handler_map = next(
            n.value
            for n in tree.body
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "HANDLERS" for t in n.targets)
        )
        job_names = [ast.literal_eval(k) for k in handler_map.keys]
        launch = load_source("_p11_t09_real_launch", "app/services/task_contracts.py")
        modules = {
            "app": package("app", root / "app"),
            "app.agent": package("app.agent", root / "app/agent"),
            "app.core": package("app.core", root / "app/core"),
            "app.services": package("app.services", root / "app/services"),
            "app.services.ai": package("app.services.ai", root / "app/services/ai"),
            "app.core.permissions": self.perms,
            "app.core.access_catalog": self.catalog,
            "app.agent.confirmation": self.confirmation,
            "app.agent.tool_contracts": self.contracts,
            "app.services.llm_tool_serialization": self.wire,
            "app.models": module_with(AgentScenario=Model, LLMModel=Model, LLMProvider=Model),
            "app.models.managers": package("app.models.managers", root / "app/models/managers"),
            "app.models.managers.llm_model_manager": module_with(LLMModelManager=Model),
            "app.services.ai.llm_client": module_with(LLMClientFactory=Model, journal_chat_attempt=AsyncMock()),
            "app.services.ai.reporting": module_with(ReportAggregator=Model),
            "app.services.task_contracts": launch,
            "app.jobs": package("app.jobs", root / "app/jobs"),
            "app.jobs.handlers": module_with(HANDLERS=dict.fromkeys(job_names)),
        }
        preloaded_config = sys.modules.get("app.core.config")
        with (
            patch.dict(sys.modules, modules),
            patch("socket.socket.connect", side_effect=AssertionError("No network")),
            patch("socket.getaddrinfo", side_effect=AssertionError("No DNS")),
        ):
            # pytest's root conftest may have loaded settings before this source-only check.
            # Remove that cached module to keep the import tripwire cold; patch.dict restores it.
            sys.modules.pop("app.core.config", None)
            for name in list(sys.modules):
                if name == "app.agent.tools" or name.startswith("app.agent.toolset"):
                    sys.modules.pop(name)
            tools = importlib.import_module("app.agent.tools")
            toolset = importlib.import_module("app.agent.toolset")
            expected = set()
            for path in (root / "app/agent/toolset").glob("*.py"):
                for node in ast.walk(ast.parse(path.read_text())):
                    if isinstance(node, ast.AsyncFunctionDef):
                        for decorator in node.decorator_list:
                            if isinstance(decorator, ast.Call) and getattr(decorator.func, "id", "") == "tool":
                                expected.add(
                                    next(ast.literal_eval(k.value) for k in decorator.keywords if k.arg == "name")
                                )
            self.assertEqual(set(tools.TOOL_REGISTRY), expected)
            for spec in tools.all_tools():
                module = sys.modules[spec.handler.__module__]
                self.assertTrue(spec.handler.__module__.startswith("app.agent.toolset."))
                self.assertIs(spec.handler, getattr(module, spec.handler.__name__))
            before = dict(tools.TOOL_REGISTRY)
            self.assertIs(importlib.import_module("app.agent.toolset"), toolset)
            self.assertEqual(dict(tools.TOOL_REGISTRY), before)
            self.assertNotIn("app.core.config", set(sys.modules) - set(modules))
        self.assertIs(sys.modules.get("app.core.config"), preloaded_config)

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
