"""Selected VK task authorization and handler binding; no app bootstrap or DB."""

import ast
import builtins
import contextlib
import enum
import logging
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

from tests.source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]


def _module(name, **attrs):
    module = ModuleType(name)
    module.__path__ = []
    module.__dict__.update(attrs)
    return module


def _load_task_access():
    access = load_isolated_source("_p8_t38_execution_vk_access", ROOT / "app/services/social/vk_access.py")

    def parse_plan(raw):
        return SimpleNamespace(
            platforms=tuple(raw.get("platforms", ["vk"])),
            collection=SimpleNamespace(
                queries=(),
                includes_comments=False,
                engagement_types=(),
                discovery=SimpleNamespace(queries=(), people=False, communities=False, city=None),
                city=None,
            ),
        )

    plan_module = _module("app.services.task_plans", parse_task_plan=parse_plan)
    # All calls in this contract need only a deterministic allowed/denied verdict.
    verdict = SimpleNamespace(allowed=True, reason="local_policy_allowed")
    checker = AsyncMock(return_value=verdict)
    catalog = AsyncMock(return_value={})
    connections = _module(
        "app.services.social.vk_connections",
        check_selected_access=checker,
        connection_catalog=catalog,
        resolve_selected_token=AsyncMock(return_value="opaque-test-token"),
    )
    ambient = {"actor": None}
    permission_state = {"actor": None, "grants": None}

    @contextlib.contextmanager
    def permission_scope(actor, *, member_grants):
        old = (permission_state["actor"], permission_state["grants"])
        permission_state.update(actor=actor, grants=member_grants)
        try:
            yield
        finally:
            permission_state.update(actor=old[0], grants=old[1])

    permissions = _module(
        "app.core.permissions",
        get_current_user=lambda: ambient["actor"],
        permission_scope=permission_scope,
    )
    actor = SimpleNamespace(id=41, is_active=True)
    user_result = {"actor": actor}

    async def get_user(*, id, is_active):
        if id == actor.id:
            return user_result["actor"]
        return SimpleNamespace(id=id, is_active=is_active)

    user_objects = SimpleNamespace(get=AsyncMock(side_effect=get_user))
    models = _module("app.models", User=SimpleNamespace(objects=user_objects))
    member = SimpleNamespace(tenant_id=8)

    class ActionType(str, enum.Enum):
        ANALYZE = "analyze"
        UPDATE = "update"

    memberships = SimpleNamespace(
        web_memberships=AsyncMock(return_value=[member]),
        member_grants=AsyncMock(return_value={("source", ActionType.ANALYZE), ("agenttask", ActionType.UPDATE)}),
    )
    tenancy = _module("app.models.managers.tenant_manager", tenant_users=memberships)

    types = _module("app.types", ActionType=ActionType)
    helper = load_isolated_source(
        "_p8_t38_execution_task_access",
        ROOT / "app/services/social/vk_task_access.py",
        {
            "app.services.social.vk_access": access,
            "app.core.permissions": permissions,
            "app.models": models,
            "app.models.managers.tenant_manager": tenancy,
            "app.services.social.vk_connections": connections,
            "app.services.task_plans": plan_module,
            "app.types": types,
        },
    )
    return SimpleNamespace(
        access=access,
        helper=helper,
        checker=checker,
        ambient=ambient,
        permission_state=permission_state,
        actor=actor,
        user_result=user_result,
        user_objects=user_objects,
        memberships=memberships,
        member=member,
        verdict=verdict,
    )


def _load_actual_handler(env, *, snapshot, task, task_payload_data, core):
    source = ROOT / "app/jobs/handlers.py"
    tree = ast.parse(source.read_text())
    names = {"handle_collect", "_task_personal_owner"}
    functions = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    if {node.name for node in functions} != names:
        raise AssertionError("expected handler functions missing from source")

    async def load_task(_task_id):
        return task

    def effective_snapshot(_payload):
        return snapshot

    def task_payload(_task):
        return task_payload_value

    task_payload_value = task_payload_data
    payer = AsyncMock(return_value=SimpleNamespace(user_id=52))
    owner_resolution = AsyncMock(side_effect=lambda source: source.params["token_owner"])
    payer_module = _module("app.services.billing.payer", resolve_scheduled_task_payer=payer)
    owner_module = _module("app.services.social.owner", resolve_source_owner=owner_resolution)
    tenant_context = _module("app.core.tenant_context", current_tenant_id=lambda: 8)
    task_access = _module(
        "app.services.social.vk_task_access",
        authorized_vk_task_scope=env.helper.authorized_vk_task_scope,
    )
    imports = {
        "app.core.tenant_context": tenant_context,
        "app.services.social.vk_task_access": task_access,
        "app.services.billing.payer": payer_module,
        "app.services.social.owner": owner_module,
    }

    def local_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level:
            import importlib.util

            name = importlib.util.resolve_name("." * level + name, (globals or {}).get("__package__"))
        if name in imports:
            return imports[name]
        return normal_import(name, globals, locals, fromlist, 0)

    normal_import = builtins.__import__
    scope = {
        "__name__": "_p8_t38_handlers_under_test",
        "__package__": "app.jobs",
        "__builtins__": dict(vars(builtins), __import__=local_import),
        "_effective_replay_snapshot": effective_snapshot,
        "_load_task": load_task,
        "_task_payload": task_payload,
        "_handle_collect_with_access": core,
        "_log_id": lambda value: value if type(value) is int and value > 0 else None,
        "_error_kind": lambda _error: "integration_error",
        "logger": logging.getLogger("p8_t38_handler_test"),
    }
    isolated = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *functions],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(isolated), str(source), "exec"), scope)
    return SimpleNamespace(
        handle_collect=scope["handle_collect"],
        payer=payer,
        owner_resolution=owner_resolution,
    )


class SelectedTaskScopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = _load_task_access()
        self.raw = {"platforms": ["vk"], "vk_access": {"mode": "api"}}

    async def _enter(self):
        return self.env.helper.authorized_vk_task_scope(self.raw, tenant_id=8, actor_id=41)

    async def test_ambient_actor_mismatch_denies_before_database_or_preflight(self):
        self.env.ambient["actor"] = SimpleNamespace(id=99)

        with self.assertRaisesRegex(PermissionError, "vk_task_actor_mismatch"):
            async with await self._enter():
                self.fail("authorization body must not run")

        self.env.user_objects.get.assert_not_awaited()
        self.env.checker.assert_not_awaited()

    async def test_missing_update_or_analyze_grant_denies_before_preflight(self):
        cases = (
            {("source", "analyze")},
            {("agenttask", "update")},
        )
        for grants in cases:
            with self.subTest(grants=grants):
                self.env.checker.reset_mock()
                self.env.memberships.member_grants.return_value = grants
                with self.assertRaisesRegex(PermissionError, "vk_task_execution_permission_required"):
                    async with await self._enter():
                        self.fail("authorization body must not run")
                self.env.checker.assert_not_awaited()

    async def test_removed_user_or_membership_denies_before_preflight(self):
        self.env.user_result["actor"] = None
        with self.assertRaisesRegex(PermissionError, "vk_task_execution_permission_required"):
            async with await self._enter():
                self.fail("removed user must not execute")
        self.env.checker.assert_not_awaited()

        self.env.user_result["actor"] = self.env.actor
        self.env.memberships.web_memberships.return_value = []
        self.env.memberships.member_grants.return_value = set()
        with self.assertRaisesRegex(PermissionError, "vk_task_execution_permission_required"):
            async with await self._enter():
                self.fail("removed membership must not execute")
        self.env.checker.assert_not_awaited()

    async def test_capability_denial_prevents_body(self):
        self.env.checker.return_value = SimpleNamespace(allowed=False, reason="scope_missing")
        entered = False
        with self.assertRaisesRegex(PermissionError, "scope_missing"):
            async with await self._enter():
                entered = True
        self.assertFalse(entered)

    async def test_success_binds_actor_and_connection_then_resets_on_exception(self):
        self.env.checker.return_value = SimpleNamespace(allowed=True, reason="local_policy_allowed")
        self.assertIsNone(self.env.helper.selected_task_access())
        with self.assertRaisesRegex(RuntimeError, "body failed"):
            async with await self._enter():
                bound = self.env.helper.selected_task_access()
                self.assertEqual(bound.selection.mode, "api")
                self.assertEqual((bound.tenant_id, bound.actor_id), (8, 41))
                self.assertEqual(self.env.permission_state["actor"], self.env.actor)
                raise RuntimeError("body failed")
        self.assertIsNone(self.env.helper.selected_task_access())
        self.assertIsNone(self.env.permission_state["actor"])


class CollectHandlerBindingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = _load_task_access()
        self.plan = {"platforms": ["vk"], "vk_access": {"mode": "api"}}
        self.task = SimpleNamespace(id=7, tenant_id=8)

    async def _handler(self, *, payload, snapshot=None, task_payload=None, access_allowed=True, selected=True):
        self.env.checker.return_value = SimpleNamespace(
            allowed=access_allowed,
            reason="local_policy_allowed" if access_allowed else "scope_missing",
        )
        core = AsyncMock(return_value={"collected": 3})

        async def inspect_core(*args, **kwargs):
            if selected:
                self.assertIsNotNone(self.env.helper.selected_task_access())
            else:
                self.assertIsNone(self.env.helper.selected_task_access())
            return await core(*args, **kwargs)

        handler = _load_actual_handler(
            self.env,
            snapshot=snapshot,
            task=self.task,
            task_payload_data=task_payload or {"task_plan": self.plan},
            core=inspect_core,
        )
        return handler, core

    async def test_persisted_plan_uses_trusted_initiator_and_binds_before_core(self):
        payload = {"agent_task_id": 7, "_trusted_initiated_by_user_id": 41}
        handler, core = await self._handler(payload=payload)

        result = await handler.handle_collect(payload)

        self.assertEqual(result, {"collected": 3})
        core.assert_awaited_once()
        self.env.checker.assert_awaited_once()
        handler.owner_resolution.assert_awaited_once()
        source = handler.owner_resolution.await_args.args[0]
        self.assertEqual(source.params["token_owner"], 41)
        self.assertIsNone(self.env.helper.selected_task_access())

    async def test_admitted_snapshot_uses_scheduled_payer_for_actor_binding(self):
        snapshot = {"task_payload": {"task_plan": self.plan}, "task_id": 19}
        payload = {"agent_task_id": 7}
        handler, core = await self._handler(payload=payload, snapshot=snapshot)

        result = await handler.handle_collect(payload)

        self.assertEqual(result, {"collected": 3})
        handler.payer.assert_awaited_once_with(task_id=19, tenant_id=8)
        source = handler.owner_resolution.await_args.args[0]
        self.assertEqual(source.params["token_owner"], 52)
        core.assert_awaited_once()

    async def test_capability_denial_prevents_collection_core(self):
        payload = {"agent_task_id": 7, "_trusted_initiated_by_user_id": 41}
        handler, core = await self._handler(payload=payload, access_allowed=False)

        with self.assertRaisesRegex(PermissionError, "scope_missing"):
            await handler.handle_collect(payload)

        core.assert_not_awaited()
        self.assertIsNone(self.env.helper.selected_task_access())

    async def test_legacy_plan_without_selection_preserves_existing_core_path(self):
        payload = {"agent_task_id": 7}
        handler, core = await self._handler(
            payload=payload, task_payload={"task_plan": {"platforms": ["vk"]}}, selected=False
        )

        result = await handler.handle_collect(payload)

        self.assertEqual(result, {"collected": 3})
        core.assert_awaited_once()
        self.env.user_objects.get.assert_not_awaited()
        self.env.checker.assert_not_awaited()
        self.assertIsNone(self.env.helper.selected_task_access())
