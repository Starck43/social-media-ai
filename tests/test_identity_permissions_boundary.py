"""Actual-source boundary tests with enum/infrastructure imports isolated.

Run: python tests/test_identity_permissions_boundary.py (no DB, no transports).
Pytest still uses the repository DB conftest; this file does not certify ORM/ASGI.
"""

import importlib.util
import sys
import types
import unittest
from enum import Enum
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


def module_with(**values):
    module = types.ModuleType("isolated_import")
    module.__dict__.update(values)
    return module


class BoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Actual enum definitions; only the DB decorator is a no-op stub.
        class DatabaseEnum(Enum):
            pass

        with patch.dict(sys.modules, {
            "app.utils.db_enums": module_with(DatabaseEnum=DatabaseEnum, database_enum=lambda cls: cls),
        }):
            enums = load_source("_boundary_user_types", "app/types/enums/user_types.py")
        self.action = enums.ActionType
        self.role = enums.UserRoleType
        # Starlette is only a typing dependency of tenant_context.
        modules = {"starlette.types": module_with(ASGIApp=object, Receive=object, Scope=object, Send=object)}
        with patch.dict(sys.modules, modules):
            self.tenant = load_source("_boundary_tenant_context", "app/core/tenant_context.py")
        modules = {
            "app.types": module_with(ActionType=self.action, UserRoleType=self.role),
            "app.core.tenant_context": self.tenant,
        }
        with patch.dict(sys.modules, modules):
            self.perms = load_source("_boundary_permissions", "app/core/permissions.py")
        with patch.dict(sys.modules, {**modules, "app.core.permissions": self.perms}):
            self.web = load_source("_boundary_web_perms", "app/web/perms.py")
        self.user = SimpleNamespace(
            is_active=True, is_superuser=False, has_perm_for=lambda *args: False,
            _is_superuser_role=lambda: False, role=None,
        )

    def test_anonymous_denied_with_and_without_owner_scope(self):
        p = self.perms
        self.assertFalse(p.has_permission(None, "source", "delete"))
        with self.tenant.tenant_scope(31), p.permission_scope(None, is_owner=True):
            self.assertFalse(p.has_permission(None, "source", "delete"))
            self.assertFalse(p.has_permission_by_codename(None, "llmmodel.create"))
            self.assertFalse(p.has_role(None, "ADMIN"))

    def test_owner_configures_only_three_workspace_models(self):
        p = self.perms
        with self.tenant.tenant_scope(31), p.permission_scope(self.user, is_owner=True):
            for model in ("source", "agenttask", "agentscenario"):
                self.assertTrue(p.has_permission(self.user, model, "create"))
            for model in ("llmmodel", "llmprovider", "user", "role", "permission", "job", "tenant", "unknown"):
                self.assertFalse(p.has_permission(self.user, model, "delete"), model)
            self.assertFalse(p.has_role(self.user, "ADMIN"))

    def test_owner_grant_does_not_follow_tenant_or_subject_switch(self):
        p = self.perms
        with self.tenant.tenant_scope(31), p.permission_scope(self.user, is_owner=True):
            other = SimpleNamespace(is_active=True, has_perm_for=lambda *args: False)
            self.assertFalse(p.has_permission(other, "source", "delete"))
            with self.tenant.tenant_scope(32):
                self.assertFalse(p.has_permission(self.user, "source", "delete"))
            self.assertTrue(p.has_permission(self.user, "source", "delete"))
        with p.permission_scope(self.user, is_owner=True):
            self.assertFalse(p.has_permission(self.user, "source", "delete"))

    def test_inactive_owner_denied(self):
        self.user.is_active = False
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user, is_owner=True):
            self.assertFalse(self.perms.has_permission(self.user, "source", "create"))
            self.assertFalse(self.perms.has_role(self.user, "ADMIN"))

    def test_malformed_rights_do_not_gain_owner_or_operator_bypass(self):
        p = self.perms
        for bypass in (False, True):
            with self.tenant.tenant_scope(31, bypass=bypass), p.permission_scope(self.user, is_owner=True):
                for codename in (None, {}, "", "source.", ".create", "source.unknown", "a.source.create"):
                    self.assertFalse(p.has_permission_by_codename(self.user, codename))
                self.assertFalse(p.has_permission(self.user, "source", "unknown"))
                self.assertFalse(p.has_role(self.user, "UNKNOWN"))

    def test_explicit_operator_scope_and_real_role_rights_preserved(self):
        with self.tenant.tenant_scope(bypass=True):
            self.assertTrue(self.perms.has_permission(None, "llmmodel", "delete"))
            self.assertTrue(self.perms.has_role(None, "ADMIN"))
        self.user.has_perm_for = lambda model, action: model == "llmmodel" and action == self.action.VIEW
        self.assertTrue(self.perms.has_permission(self.user, "llmmodel", "view"))
        self.assertFalse(self.perms.has_permission(self.user, "llmmodel", "delete"))
        self.user.has_perm_for = lambda *args: (_ for _ in ()).throw(RuntimeError("detached"))
        self.assertFalse(self.perms.has_permission(self.user, "llmmodel", "view"))

    def test_cli_operator_authority_survives_data_narrowing_not_interactive_scope(self):
        p = self.perms
        with p.operator_permission_scope(), self.tenant.tenant_scope(31):
            self.assertTrue(p.has_permission(None, "agenttask", "create"))
            self.assertTrue(p.has_role(None, "ADMIN"))
            with p.permission_scope(None):
                self.assertFalse(p.has_permission(None, "agenttask", "create"))
                self.assertFalse(p.has_role(None, "ADMIN"))
        self.assertFalse(p.has_permission(None, "agenttask", "create"))

    def test_service_grant_is_exact_tenant_model_action_not_role(self):
        p = self.perms
        with self.tenant.tenant_scope(31), p.service_permission_scope("source", "update"):
            self.assertTrue(p.has_permission(None, "source", "update"))
            self.assertFalse(p.has_permission(None, "source", "delete"))
            self.assertFalse(p.has_permission(None, "agenttask", "update"))
            self.assertFalse(p.has_role(None, "ADMIN"))
            with self.tenant.tenant_scope(32):
                self.assertFalse(p.has_permission(None, "source", "update"))
            with p.permission_scope(None):
                self.assertFalse(p.has_permission(None, "source", "update"))
            self.assertTrue(p.has_permission(None, "source", "update"))
        self.assertFalse(p.has_permission(None, "source", "update"))

    def test_service_grant_rejects_global_models_and_missing_tenant(self):
        with self.assertRaises(ValueError), self.perms.service_permission_scope("source", "update"):
            pass
        with self.tenant.tenant_scope(31):
            with self.assertRaises(ValueError), self.perms.service_permission_scope("llmmodel", "create"):
                pass

    def test_scope_restores_after_exception(self):
        p = self.perms
        with self.tenant.tenant_scope(31):
            with self.assertRaises(RuntimeError), p.permission_scope(self.user, is_owner=True):
                with p.service_permission_scope("source", "update"):
                    raise RuntimeError("stop")
            self.assertIsNone(p.get_current_user())
            self.assertFalse(p.has_permission(None, "source", "update"))
            with p.permission_scope(self.user):
                self.assertFalse(p.has_permission(self.user, "source", "create"))

    async def test_sync_async_decorators_block_before_side_effect(self):
        p = self.perms
        calls = []

        @p.require_permission("source", "delete")
        def sync(*, current_user=None):
            calls.append("sync")

        @p.require_permission("llmmodel", "create")
        async def async_write(*, current_user=None):
            calls.append("async")

        with self.tenant.tenant_scope(31), p.permission_scope(None, is_owner=True):
            with self.assertRaises(p.PermissionDeniedError):
                sync()
            with self.assertRaises(p.PermissionDeniedError):
                await async_write()
        with self.tenant.tenant_scope(31), p.permission_scope(self.user, is_owner=True):
            with self.assertRaises(p.PermissionDeniedError):
                sync(current_user=None)
            with self.assertRaises(p.PermissionDeniedError):
                await async_write()
        self.assertEqual(calls, [])

    async def test_api_carries_authenticated_identity_to_manager_scope(self):
        responses = []

        def response(body, status_code=200, **kwargs):
            async def send_response(scope, receive, send):
                responses.append(status_code)
            return send_response

        modules = {
            "jose": module_with(JWTError=RuntimeError, jwt=None),
            "starlette.requests": module_with(Request=lambda scope: SimpleNamespace(state=SimpleNamespace())),
            "starlette.responses": module_with(JSONResponse=response),
            "starlette.types": module_with(ASGIApp=object, Receive=object, Scope=object, Send=object),
            "app.core.config": module_with(settings=SimpleNamespace()),
            "app.core.permissions": self.perms,
            "app.core.tenant_context": self.tenant,
        }
        with patch.dict(sys.modules, modules):
            api = load_source("_boundary_api_scope", "app/core/api_scope.py")
        observations = []

        async def downstream(scope, receive, send):
            observations.append((self.perms.get_current_user(), self.tenant.current_tenant_id()))
            self.assertFalse(self.perms.has_permission_by_codename(self.perms.get_current_user(), "source.delete"))

        middleware = api.ApiScopeMiddleware(downstream)
        middleware._authenticate = AsyncMock(return_value=self.user)
        middleware._resolve_tenant = AsyncMock(return_value=(31, None))
        await middleware({"type": "http", "path": "/api/v1/sources"}, None, None)
        self.assertEqual(observations, [(self.user, 31)])
        self.assertIsNone(self.perms.get_current_user())
        self.assertIsNone(self.tenant.current_tenant_id())
        self.user.is_active = False
        await middleware({"type": "http", "path": "/api/v1/sources"}, None, None)
        self.assertEqual(responses, [401])
        self.assertEqual(len(observations), 1)

    def test_web_owner_has_same_global_boundary(self):
        membership = SimpleNamespace(tenant_id=31, is_owner=True)
        w = self.web.WebPerms(self.user, [membership], 31)
        self.assertTrue(w.can("agenttask", "create"))
        for model in ("llmmodel", "llmprovider", "user", "role", "job", "unknown"):
            self.assertFalse(w.can(model, "delete"))
        self.assertFalse(w.can("source", "unknown"))
        self.assertFalse(self.web.WebPerms(None, [membership], 31).can("source", "create"))
        self.assertFalse(self.web.WebPerms(self.user, [membership], 32).can("source", "create"))


    def _settings_membership(self, **changes):
        values = dict(tenant_id=31, user_id=42, channel="web", is_active=True, is_owner=True)
        values.update(changes)
        self.user.id = 42
        return SimpleNamespace(**values)

    def test_web_settings_owner_capability_does_not_grant_global_rights(self):
        membership = self._settings_membership()
        w = self.web.WebPerms(self.user, [membership], 31)
        self.assertTrue(w.can_manage_workspace(31))
        for model in ("tenant", "user", "role", "permission", "job", "llmmodel", "llmprovider"):
            self.assertFalse(w.can(model, "update"), model)
        with self.tenant.tenant_scope(31), self.perms.permission_scope(self.user, is_owner=True):
            self.assertFalse(self.perms.has_permission(self.user, "tenant", "update"))

    def test_web_settings_requires_active_bound_web_membership(self):
        for changes in (
            {"user_id": 43}, {"user_id": None}, {"channel": "telegram"},
            {"is_active": False}, {"tenant_id": 32}, {"is_owner": False},
        ):
            with self.subTest(changes=changes):
                membership = self._settings_membership(**changes)
                self.assertFalse(self.web.WebPerms(self.user, [membership], 31).can_manage_workspace(31))
        self.assertFalse(self.web.WebPerms(self.user, [], 31).can_manage_workspace(31))

    def test_web_settings_owner_capability_does_not_follow_target_switch(self):
        memberships = [self._settings_membership(), self._settings_membership(tenant_id=32)]
        w = self.web.WebPerms(self.user, memberships, 31)
        self.assertFalse(w.can_manage_workspace(32))
        self.assertFalse(self.web.WebPerms(self.user, memberships).can_manage_workspace(31))
        self.user.has_perm_for = lambda *args: True
        self.assertFalse(w.can_manage_workspace(32))

    def test_web_settings_anonymous_inactive_and_unbound_users_are_denied(self):
        membership = self._settings_membership()
        self.assertFalse(self.web.WebPerms(None, [membership], 31).can_manage_workspace(31))
        self.user.is_active = False
        self.assertFalse(self.web.WebPerms(self.user, [membership], 31).can_manage_workspace(31))
        self.user.is_active = True
        self.user.id = None
        self.assertFalse(self.web.WebPerms(self.user, [membership], 31).can_manage_workspace(31))

    def test_web_settings_member_keeps_actual_role_right_for_bound_workspace(self):
        membership = self._settings_membership(is_owner=False)
        self.user.has_perm_for = lambda model, action: model == "tenant" and action == self.action.UPDATE
        w = self.web.WebPerms(self.user, [membership], 31)
        self.assertTrue(w.can_manage_workspace(31))
        self.assertFalse(w.can_manage_workspace(32))
        self.user.has_perm_for = lambda *args: (_ for _ in ()).throw(RuntimeError("detached"))
        self.assertFalse(w.can_manage_workspace(31))

    def test_web_settings_superuser_still_needs_active_identity_and_concrete_target(self):
        self.user.is_superuser = True
        w = self.web.WebPerms(self.user)
        self.assertTrue(w.can_manage_workspace(32))
        for target in (None, 0, -1, True, "32"):
            self.assertFalse(w.can_manage_workspace(target), target)
        self.user.is_active = False
        self.assertFalse(w.can_manage_workspace(32))


if __name__ == "__main__":
    unittest.main()
