"""Offline source-slice audit probes; expected failures are OPEN defects, not fixes.

Run with a separate empty venv: python tests/test_interface_audit_probes.py -v.
Only stdlib is used. No application module, conftest, settings, DB or provider
is imported. AST-selected production functions run against explicit doubles.
Unexpected success requires reviewing/removing the corresponding audit finding.
"""

from __future__ import annotations

import ast
import asyncio
import re
import sys
import unittest
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def tree(path):
    return ast.parse((ROOT / path).read_text())


def source_slice(path, names, namespace=None):
    """Compile exact top-level definitions, minus registration decorators only."""
    namespace = dict(namespace or {})
    selected = []
    for node in tree(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names:
            if not isinstance(node, ast.ClassDef):
                node.decorator_list = []
            selected.append(node)
    if {n.name for n in selected} != set(names):
        raise AssertionError(f"Missing source definitions in {path}")
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *selected],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), path, "exec"), namespace)
    return namespace


@contextmanager
def modules(**exports):
    """Replace precisely named import boundaries; never import the real app."""
    stubs = {}
    for name, attrs in exports.items():
        module = ModuleType(name)
        module.__dict__.update(attrs)
        stubs[name] = module
    with patch.dict(sys.modules, stubs):
        yield


def cli_namespace():
    class Exit(Exception):
        pass

    return source_slice(
        "cli/commands/llm.py",
        {"provider_list", "model_test"},
        {
            "typer": NS(Option=lambda *a, **kw: None, Argument=lambda *a, **kw: None, Exit=Exit),
            "_run": asyncio.run,
            "rprint": Mock(),
            "Table": lambda **kw: NS(add_column=Mock(), add_row=Mock()),
        },
    )


class OfflineAuditTests(unittest.TestCase):
    def test_slices_do_not_import_application(self):
        before = {name for name in sys.modules if name == "app" or name.startswith("app.")}
        cli_namespace()
        after = {name for name in sys.modules if name == "app" or name.startswith("app.")}
        self.assertEqual(before, after)

    def test_tool_names_unique_and_schema_arguments_match_handlers(self):
        names = []
        for path in sorted((ROOT / "app/agent/toolset").glob("*.py")):
            for node in ast.parse(path.read_text()).body:
                if not isinstance(node, ast.AsyncFunctionDef):
                    continue
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Name):
                        continue
                    if decorator.func.id != "tool":
                        continue
                    kw = {k.arg: k.value for k in decorator.keywords}
                    names.append(ast.literal_eval(kw["name"]))
                    self.assertIn("required_permission", kw)
                    params = kw["parameters"]
                    props = next(v for k, v in zip(params.keys, params.values) if ast.literal_eval(k) == "properties")
                    # Dictionary spreads are shared schema fragments, not unverified fields.
                    explicit = {ast.literal_eval(k) for k in props.keys if k is not None}
                    arguments = {a.arg for a in node.args.args + node.args.kwonlyargs}
                    self.assertTrue(explicit <= arguments, (node.name, explicit - arguments))
        self.assertGreater(len(names), 20)
        self.assertEqual(len(names), len(set(names)))

    def test_provider_model_has_no_default_after_migration(self):
        provider = next(n for n in tree("app/models/llm/llm_provider.py").body if isinstance(n, ast.ClassDef))
        fields = {n.target.id for n in provider.body if isinstance(n, ast.AnnAssign)}
        self.assertNotIn("is_default", fields)
        migration = (ROOT / "migrations/versions/0091_drop_llm_provider_default_flag.py").read_text()
        self.assertIn('COLUMN = "is_default"', migration)

    @unittest.expectedFailure
    def test_provider_list_handles_current_provider_without_default(self):
        scope = cli_namespace()
        providers = NS(order_by=AsyncMock(return_value=[NS(
            id=1, name="fixture", api_format="openai", base_url="https://fixture.invalid", is_active=True
        )]))
        models_query = NS(all=AsyncMock(return_value=[]))
        with modules(**{
            "app.models": {"LLMProvider": NS(id=1, objects=providers),
                           "LLMModel": NS(objects=NS(filter=Mock(return_value=models_query)))},
            "rich.console": {"Console": lambda: NS(print=Mock())},
        }):
            scope["provider_list"]()

    @unittest.expectedFailure
    def test_cli_mock_does_not_instantiate_abstract_llm_client(self):
        client = source_slice("app/services/ai/llm_client.py", {"LLMClient"},
                              {"ABC": ABC, "abstractmethod": abstractmethod})["LLMClient"]
        scope = cli_namespace()
        manager = NS(select_related=Mock(return_value=NS(get=AsyncMock(return_value=NS(
            name="fixture", provider=NS(name="fixture")
        )))))
        with modules(**{
            "app.models": {"LLMModel": NS(objects=manager)},
            "app.admin.actions": {"LLMModelActions": NS()},
            "app.services.ai.llm_client": {"LLMClient": client},
        }):
            scope["model_test"](1, prompt="fixture", mock=True)

    @unittest.expectedFailure
    def test_cli_real_probe_target_exists(self):
        actions = next(n for n in tree("app/admin/actions.py").body if isinstance(n, ast.ClassDef))
        methods = {n.name for n in actions.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertIn("test_model_real", methods)

    @unittest.expectedFailure
    def test_model_response_contract_accepts_capability_sets(self):
        base = next(n for n in tree("app/schemas/llm_model.py").body
                    if isinstance(n, ast.ClassDef) and n.name == "LLMModelBase")
        field = next(n for n in base.body if isinstance(n, ast.AnnAssign) and n.target.id == "model_type")
        pattern = next(ast.literal_eval(k.value) for k in field.value.keywords if k.arg == "pattern")
        for value in ("video", "text,image", "text,video"):
            with self.subTest(value=value):
                self.assertIsNotNone(re.fullmatch(pattern, value))

    @unittest.expectedFailure
    def test_chat_model_schema_exposes_video_capability(self):
        node = next(n for n in tree("app/agent/toolset/llm.py").body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == "llm_model_add")
        kw = {k.arg: k.value for k in node.decorator_list[0].keywords}
        params = ast.literal_eval(kw["parameters"])
        self.assertIn("video", params["properties"]["model_type"]["enum"])

    @unittest.expectedFailure
    def test_task_list_resolves_agent_task_symbol(self):
        # The real module has only these imports; import statements are inspected,
        # not executed. Do not inject the missing symbol and conceal the defect.
        module = tree("app/agent/toolset/tasks.py")
        imports = {alias.asname or alias.name for n in module.body
                   if isinstance(n, (ast.Import, ast.ImportFrom)) for alias in n.names}
        self.assertIn("AgentTask", imports)

    @unittest.expectedFailure
    def test_credential_disable_mount_matches_documented_route(self):
        entry = tree("app/api/v1/entry.py")
        mount = next(n.value for n in entry.body if isinstance(n, ast.Expr)
                     and isinstance(n.value, ast.Call)
                     and isinstance(n.value.func, ast.Attribute)
                     and n.value.func.attr == "include_router"
                     and ast.unparse(n.value.args[0]) == "credentials.router")
        prefix = next(ast.literal_eval(k.value) for k in mount.keywords if k.arg == "prefix")
        endpoint = next(n for n in tree("app/api/v1/endpoints/credentials.py").body
                        if isinstance(n, ast.AsyncFunctionDef) and n.name == "disable_credential")
        route = ast.literal_eval(endpoint.decorator_list[0].args[0])
        self.assertEqual("/api/v1" + prefix + route, "/api/v1/credentials/{credential_id}/disable")

    @unittest.expectedFailure
    def test_workspace_model_override_reaches_chat_selection(self):
        model = NS(id=2, name="fleet-default")
        resolver = AsyncMock(return_value=model)
        tenant = NS(agent_model="chosen-fixture", agent_max_tokens=None, agent_temperature=None)
        fallback = AsyncMock(return_value={"content": "fixture"})
        scope = source_slice("app/agent/runtime.py", {"_chat"},
                             {"settings": NS(AGENT_MAX_TOKENS=100, AGENT_TEMPERATURE=0)})
        with modules(**{
            "app.core.tenant_context": {"current_tenant_id": lambda: 7},
            "app.models.managers.tenant_manager": {"tenants": NS(get=AsyncMock(return_value=tenant))},
            "app.models": {"LLMModel": NS(objects=NS(resolve_default_model=resolver))},
            "app.services.ai.llm_client": {"chat_with_fallback": fallback},
        }):
            asyncio.run(scope["_chat"]([], []))
        self.assertEqual(fallback.await_args.kwargs["preferred_model"].name, tenant.agent_model)


class TaskUpdateAuditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.task = NS(id=1, name="fixture", tenant_id=7, job_type="digest", cron_expr="0 9 * * *",
                       agent_scenario_id=42, payload={"brands": ["fixture"]})
        self.manager = NS(get=AsyncMock(return_value=self.task), update_by_id=AsyncMock())
        self.scope = source_slice("app/agent/toolset/tasks.py", {"task_update"},
                                  {"_get_job_types": lambda: ("digest",),
                                   "_check_task_sources": AsyncMock()})
        self.stubs = {
            "app.core.tenant_context": {"current_tenant_id": lambda: 7},
            "app.models": {"AgentTask": NS(objects=self.manager),
                           "Tenant": NS(objects=NS(get=AsyncMock(return_value=NS())))},
            "app.models.managers.agent_task_manager": {"AgentTaskManager": NS(
                validate_cron=lambda value: True, requires_content_dates=lambda jt: False)},
            "app.tasks.cron": {"next_run_at": lambda cron, tz: "next-fixture", "resolve_tz": lambda t: "UTC"},
        }

    async def invoke(self, **kwargs):
        with modules(**self.stubs):
            return await self.scope["task_update"]("fixture", **kwargs)

    async def test_partial_payload_preserves_existing_targets(self):
        await self.invoke(period="week")
        self.assertEqual(self.manager.update_by_id.await_args.kwargs["payload"],
                         {"brands": ["fixture"], "period": "week"})

    @unittest.expectedFailure
    async def test_unrelated_update_preserves_scenario_binding(self):
        await self.invoke(period="week")
        changes = self.manager.update_by_id.await_args.kwargs
        self.assertEqual(changes.get("agent_scenario_id", self.task.agent_scenario_id), 42)

    @unittest.expectedFailure
    async def test_cron_change_persists_actual_expression(self):
        await self.invoke(cron="0 10 * * *")
        self.assertEqual(self.manager.update_by_id.await_args.kwargs.get("cron_expr"), "0 10 * * *")


if __name__ == "__main__":
    unittest.main()
