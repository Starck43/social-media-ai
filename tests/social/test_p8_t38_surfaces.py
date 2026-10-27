"""Isolated tests of the actual task form and connection-catalog entry points."""

from __future__ import annotations

import ast
import builtins
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from tests.source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]


def _actual_function(path: str, function_name: str, namespace: dict, imports: dict):
    tree = ast.parse((ROOT / path).read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
    )
    function.decorator_list = []
    namespace = dict(namespace)
    normal_import = builtins.__import__

    def isolated_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0 and name in imports:
            if not fromlist:
                raise ImportError("Test doubles require explicit from-import")
            return imports[name]
        return normal_import(name, globals, locals, fromlist, level)

    namespace["__builtins__"] = dict(vars(builtins), __import__=isolated_import)
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), function],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(ROOT / path), "exec"), namespace)
    return namespace[function_name]


class VKTaskFormSelectionTests(unittest.TestCase):
    def setUp(self):
        config = types.ModuleType("app.core.config")
        config.settings = types.SimpleNamespace(SOCIAL_COLLECT_LIMIT=100, SOCIAL_COLLECT_HARD_LIMIT=1000)
        intent = load_isolated_source(
            "_p8_t38_surface_intent", ROOT / "app/services/collection/intent.py", {"app.core.config": config}
        )
        access = load_isolated_source("_p8_t38_surface_access", ROOT / "app/services/social/vk_access.py")
        task_plans = load_isolated_source(
            "_p8_t38_surface_plans",
            ROOT / "app/services/task_plans.py",
            {"app.services.collection.intent": intent, "app.services.social.vk_access": access},
        )
        self.build = _actual_function(
            "app/web/tasks.py",
            "_task_plan_from_form",
            {"__name__": "isolated_task_form"},
            {"app.services.task_plans": task_plans},
        )

    def call_form(self, selection_raw="", existing=None):
        return self.build(
            "research",
            ["vk"],
            "discovery",
            [],
            None,
            "collect",
            "@once",
            {"collection": {"queries": ["research"]}},
            "rolling",
            1,
            "",
            selection_raw,
            existing,
        )

    def test_blank_editor_field_retains_exact_existing_pin(self):
        selected = {"mode": "user", "credential_id": 19}

        result = self.call_form(existing=selected)

        self.assertEqual(result["vk_access"], selected)

    def test_explicit_editor_selection_changes_pin(self):
        result = self.call_form('{"mode":"api"}', existing={"mode": "user", "credential_id": 19})

        self.assertEqual(result["vk_access"], {"mode": "api"})

    def test_malformed_editor_json_is_rejected_without_echoing_input(self):
        with self.assertRaises(ValueError) as caught:
            self.call_form('{"token":"private-sentinel"')
        self.assertNotIn("private-sentinel", str(caught.exception))

    def test_invalid_editor_json_is_rejected_without_echoing_input(self):
        secret = '{"token":"private-sentinel"}'
        with self.assertRaises(ValueError) as caught:
            self.call_form(secret)
        self.assertNotIn("private-sentinel", str(caught.exception))


class ConnectionCatalogRouteTests(unittest.IsolatedAsyncioTestCase):
    async def _load_web(self):
        self.catalog = AsyncMock(side_effect=PermissionError("private-sentinel"))
        response = lambda **kwargs: types.SimpleNamespace(
            status_code=kwargs.get("status_code"), body=kwargs.get("content")
        )
        permission_module = types.SimpleNamespace(get_current_user=lambda: types.SimpleNamespace(id=7))
        connection_module = types.SimpleNamespace(connection_catalog=self.catalog)
        imports = {
            "app.core.permissions": permission_module,
            "app.services.social.vk_connections": connection_module,
        }
        self.web = _actual_function(
            "app/web/tasks.py",
            "web_vk_connections",
            {
                "__name__": "isolated_web_route",
                "guard_web": lambda *args, **kwargs: None,
                "action_tenant_id": lambda request: 10,
                "Response": response,
            },
            imports,
        )

    async def test_web_catalog_returns_bounded_403_on_revoked_membership(self):
        await self._load_web()

        result = await self.web(types.SimpleNamespace())

        self.assertEqual(result.status_code, 403)
        self.assertEqual(result.body, "vk_access_unavailable")
        self.catalog.assert_awaited_once_with(tenant_id=10, actor_id=7)

    async def test_api_catalog_returns_fixed_403_on_revoked_membership(self):
        class HTTPException(Exception):
            def __init__(self, *, status_code, detail):
                self.status_code = status_code
                self.detail = detail

        self.catalog = AsyncMock(side_effect=PermissionError("private-sentinel"))
        imports = {
            "app.core.tenant_context": types.SimpleNamespace(current_tenant_id=lambda: 10),
            "app.services.social.vk_connections": types.SimpleNamespace(connection_catalog=self.catalog),
        }
        route = _actual_function(
            "app/api/v1/endpoints/tasks.py",
            "get_vk_connections",
            {
                "__name__": "isolated_api_route",
                "HTTPException": HTTPException,
                "Depends": lambda value: value,
                "require_model_perm": lambda *args: None,
                "ActionType": types.SimpleNamespace(VIEW="view"),
            },
            imports,
        )

        with self.assertRaises(HTTPException) as caught:
            await route(types.SimpleNamespace(id=7))

        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail, "vk_access_unavailable")
        self.catalog.assert_awaited_once_with(tenant_id=10, actor_id=7)

    async def test_catalog_metadata_in_both_editors_stays_safe_and_explicit(self):
        for filename in ("tasks.html", "task_edit.html"):
            source = (ROOT / "app/web/templates/web" / filename).read_text()
            self.assertIn("selectedVkConnection()", source)
            self.assertIn("method_scopes", source)
            self.assertIn("account_id", source)
            self.assertIn("expires_at", source)
            self.assertIn("Q-5", source)
            self.assertIn("доступность методов", source)
