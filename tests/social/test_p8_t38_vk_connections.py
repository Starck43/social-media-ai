"""Mocked VK adapter tests; import and execute without app startup or a database."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]


def _module(name: str, **attrs):
    result = types.ModuleType(name)
    result.__dict__.update(attrs)
    if name in {"app", "app.core", "app.models", "app.models.managers", "app.models.identity", "app.services"}:
        result.__path__ = []
    return result


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


# Import the real modules with temporary package entries, then restore sys.modules.
with patch.dict(
    sys.modules,
    {
        "app": _module("app"),
        "app.services": _module("app.services"),
        "app.services.social": _module("app.services.social"),
    },
):
    policy = _load("app.services.social.vk_access", ROOT / "app/services/social/vk_access.py")
    connections = _load("p8_t38_vk_connections", ROOT / "app/services/social/vk_connections.py")

NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)


class FakeColumn:
    def __init__(self, name):
        self.name = name

    def desc(self):
        return self


class FakeUserCredential:
    id = FakeColumn("id")
    user_id = FakeColumn("user_id")
    is_active = FakeColumn("is_active")
    expires_at = FakeColumn("expires_at")
    meta = FakeColumn("meta")


class FakeRow:
    def __init__(self, *, user_id=3, active=True, expires_at=None, meta=None):
        self.id = 7
        self.user_id = user_id
        self.is_active = active
        self.expires_at = expires_at
        self.meta = meta or {}
        self.secret = "exact-selected-secret"

    def reveal(self):
        return self.secret


class Query:
    def __init__(self, manager):
        self.manager = manager

    def order_by(self, *args):
        self.manager.ordering = args
        return self

    def limit(self, count):
        self.manager.limit_count = count
        return self

    def values(self, *columns):
        self.manager.selected_columns = columns
        return self

    async def first(self):
        self.manager.first_calls += 1
        return self.manager.entity

    async def first_row(self):
        self.manager.first_row_calls += 1
        return self.manager.projection

    async def rows(self):
        return self.manager.projections


class FakeCredentialManager:
    def __init__(self):
        self.entity = FakeRow(
            meta={
                "scope": "wall groups",
                "vk_method_grants": ["wall.get"],
                "vk_method_scopes": {"wall.get": ["wall"]},
            }
        )
        self.projection = self.entity
        self.projections = [self.entity]
        self.filters = []
        self.selected_columns = ()
        self.limit_count = None
        self.first_calls = 0
        self.first_row_calls = 0

    def filter(self, **kwargs):
        self.filters.append(kwargs)
        return Query(self)


class VKConnectionAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.actor = SimpleNamespace(id=3)
        self.membership = SimpleNamespace(tenant_id=10, is_active=True)
        self.manager = FakeCredentialManager()
        self.user_objects = SimpleNamespace(get=AsyncMock(return_value=self.actor))
        self.tenant_objects = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id=10)))
        self.tenant_users = SimpleNamespace(
            web_memberships=AsyncMock(return_value=[self.membership]),
            member_grants=AsyncMock(return_value={("source", "analyze"), ("agenttask", "update")}),
        )
        action_type = SimpleNamespace(ANALYZE="analyze", UPDATE="update")
        user_model = SimpleNamespace(objects=self.user_objects)
        tenant_model = SimpleNamespace(objects=self.tenant_objects)
        self.settings = SimpleNamespace(
            VK_SERVICE_KEY=None,
            VK_SERVICE_METHOD_GRANTS=None,
            VK_SERVICE_GRANTED_SCOPES=None,
            VK_SERVICE_METHOD_SCOPES={},
        )
        fake_modules = {
            "app": _module("app"),
            "app.core": _module("app.core"),
            "app.core.permissions": _module("app.core.permissions", get_current_user=lambda: self.actor),
            "app.core.tenant_context": _module(
                "app.core.tenant_context",
                current_tenant_id=lambda: 10,
                is_bypass=lambda: False,
            ),
            "app.core.config": _module("app.core.config", settings=self.settings),
            "app.types": _module("app.types", ActionType=action_type),
            "app.models": _module("app.models", Tenant=tenant_model, User=user_model),
            "app.models.managers": _module("app.models.managers"),
            "app.models.managers.tenant_manager": _module(
                "app.models.managers.tenant_manager", tenant_users=self.tenant_users
            ),
            "app.models.managers.user_credential_manager": _module(
                "app.models.managers.user_credential_manager",
                user_credentials=self.manager,
            ),
            "app.models.identity": _module("app.models.identity"),
            "app.models.identity.user_credential": _module(
                "app.models.identity.user_credential", UserCredential=FakeUserCredential
            ),
            "app.services": _module("app.services"),
            "app.services.social": _module("app.services.social"),
        }
        self.runtime_modules = patch.dict(sys.modules, fake_modules)
        self.runtime_modules.start()
        self.addCleanup(self.runtime_modules.stop)
        self.selection = policy.VKSelection("user", 7)

    async def test_explicit_personal_grant_passes_with_real_fresh_snapshot_path(self):
        result = await connections.check_selected_access(
            self.selection,
            tenant_id=10,
            actor_id=3,
            required_methods=frozenset({"wall.get"}),
            required_scopes=frozenset({"wall"}),
        )
        self.assertTrue(result.allowed)
        self.assertEqual(self.manager.filters[-1], {"id": 7, "user_id": 3, "platform": "vk", "kind": "user_token"})
        selected = {column.name for column in self.manager.selected_columns}
        self.assertNotIn("secret_encrypted", selected)

    async def test_removed_membership_is_rechecked_and_denied(self):
        self.tenant_users.web_memberships.return_value = []
        with self.assertRaisesRegex(connections.VKConnectionDenied, "vk_access_membership_required"):
            await connections.check_selected_access(
                self.selection, tenant_id=10, actor_id=3, required_methods=frozenset({"wall.get"})
            )
        self.assertEqual(self.manager.filters, [])

    async def test_inactive_user_and_workspace_are_denied(self):
        self.user_objects.get.return_value = None
        with self.assertRaisesRegex(connections.VKConnectionDenied, "actor_or_workspace_inactive"):
            await connections.check_selected_access(
                self.selection, tenant_id=10, actor_id=3, required_methods=frozenset({"wall.get"})
            )
        self.user_objects.get.return_value = self.actor
        self.tenant_objects.get.return_value = None
        with self.assertRaisesRegex(connections.VKConnectionDenied, "actor_or_workspace_inactive"):
            await connections.check_selected_access(
                self.selection, tenant_id=10, actor_id=3, required_methods=frozenset({"wall.get"})
            )

    async def test_fresh_source_analysis_permission_is_required(self):
        self.tenant_users.member_grants.return_value = set()
        with self.assertRaisesRegex(connections.VKConnectionDenied, "analysis_permission_required"):
            await connections.check_selected_access(
                self.selection, tenant_id=10, actor_id=3, required_methods=frozenset({"wall.get"})
            )
        self.assertEqual(self.manager.filters, [])

    async def test_another_members_credential_is_not_selected(self):
        self.manager.projection = FakeRow(user_id=4)
        result = await connections.check_selected_access(
            self.selection, tenant_id=10, actor_id=3, required_methods=frozenset({"wall.get"})
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "connection_owner_not_member")
        self.assertEqual(self.manager.filters[-1]["user_id"], 3)

    async def test_fresh_task_update_permission_is_required(self):
        self.tenant_users.member_grants.return_value = {("source", "analyze")}
        with self.assertRaisesRegex(connections.VKConnectionDenied, "task_permission_required"):
            await connections.check_selected_access(
                self.selection, tenant_id=10, actor_id=3, required_methods=frozenset({"wall.get"})
            )
        self.assertEqual(self.manager.filters, [])

    async def test_oauth_scope_does_not_imply_method_grant(self):
        self.manager.projection = FakeRow(meta={"scope": "wall"})
        result = await connections.check_selected_access(
            self.selection, tenant_id=10, actor_id=3, required_methods=frozenset({"wall.get"})
        )
        self.assertEqual(result.reason, "method_grants_unknown")

    async def test_missing_scope_denies_explicit_method_grant(self):
        self.manager.projection = FakeRow(
            meta={
                "scope": "groups",
                "vk_method_grants": ["wall.get"],
                "vk_method_scopes": {"wall.get": ["wall"]},
            }
        )
        result = await connections.check_selected_access(
            self.selection,
            tenant_id=10,
            actor_id=3,
            required_methods=frozenset({"wall.get"}),
        )
        self.assertEqual(result.reason, "scope_missing")

    async def test_unknown_method_scope_declaration_denies(self):
        self.manager.projection = FakeRow(
            meta={"scope": "wall", "vk_method_grants": ["wall.get"], "vk_method_scopes": {}}
        )
        result = await connections.check_selected_access(
            self.selection, tenant_id=10, actor_id=3, required_methods=frozenset({"wall.get"})
        )
        self.assertEqual(result.reason, "scope_requirements_unknown")

    async def test_service_access_uses_explicit_configured_scope_requirements(self):
        self.settings.VK_SERVICE_KEY = "configured-service-token"
        self.settings.VK_SERVICE_METHOD_GRANTS = ["wall.get"]
        self.settings.VK_SERVICE_GRANTED_SCOPES = ["wall"]
        self.settings.VK_SERVICE_METHOD_SCOPES = {"wall.get": ["wall"]}
        result = await connections.check_selected_access(
            policy.VKSelection("api"), tenant_id=10, actor_id=3, required_methods=frozenset({"wall.get"})
        )
        self.assertTrue(result.allowed)
        self.assertEqual(self.manager.filters, [])

    async def test_expired_and_revoked_rows_are_denied(self):
        self.manager.projection = FakeRow(
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            meta={"vk_method_grants": ["wall.get"]},
        )
        expired = await connections.check_selected_access(
            self.selection, tenant_id=10, actor_id=3, required_methods=frozenset({"wall.get"})
        )
        self.assertEqual(expired.reason, "connection_expired")
        self.manager.projection = FakeRow(active=False, meta={"vk_method_grants": ["wall.get"]})
        revoked = await connections.check_selected_access(
            self.selection, tenant_id=10, actor_id=3, required_methods=frozenset({"wall.get"})
        )
        self.assertEqual(revoked.reason, "connection_revoked")

    async def test_tenant_context_mismatch_denies_before_vault_query(self):
        with patch.dict(
            sys.modules,
            {
                "app.core.tenant_context": _module(
                    "app.core.tenant_context", current_tenant_id=lambda: 11, is_bypass=lambda: False
                )
            },
        ):
            with self.assertRaisesRegex(connections.VKConnectionDenied, "actor_or_workspace_required"):
                await connections.check_selected_access(
                    self.selection, tenant_id=10, actor_id=3, required_methods=frozenset({"wall.get"})
                )
        self.assertEqual(self.manager.filters, [])

    async def test_fresh_rights_and_exact_row_are_rechecked_before_reveal(self):
        result = await connections.check_selected_access(
            self.selection, tenant_id=10, actor_id=3, required_methods=frozenset({"wall.get"})
        )
        self.assertTrue(result.allowed)
        token = await connections.resolve_selected_token(
            self.selection, tenant_id=10, actor_id=3, required_methods=frozenset({"wall.get"})
        )
        self.assertEqual(token, "exact-selected-secret")
        self.assertEqual(self.manager.filters[-1], {"id": 7, "user_id": 3, "platform": "vk", "kind": "user_token"})
        self.assertEqual(self.tenant_users.web_memberships.await_count, 2)
        self.assertEqual(self.manager.first_row_calls, 1)
        self.assertEqual(self.manager.first_calls, 1)

    async def test_permission_revocation_between_check_and_reveal_denies_token(self):
        # A prior preview does not survive the fresh workspace-grant read.
        self.tenant_users.member_grants.side_effect = [
            {("source", "analyze"), ("agenttask", "update")},
            set(),
        ]
        checked = await connections.check_selected_access(
            self.selection, tenant_id=10, actor_id=3, required_methods=frozenset({"wall.get"})
        )
        self.assertTrue(checked.allowed)
        with self.assertRaisesRegex(connections.VKConnectionDenied, "analysis_permission_required"):
            await connections.resolve_selected_token(
                self.selection,
                tenant_id=10,
                actor_id=3,
                required_methods=frozenset({"wall.get"}),
            )

    async def test_scope_and_method_parsers_drop_unrecognized_values(self):
        self.assertEqual(connections._safe_scopes("wall unrelated secret"), frozenset({"wall"}))
        self.assertIsNone(connections._method_grants(["wall.get", "untrusted.method"]))
        self.assertEqual(connections._method_grants(["wall.get"]), frozenset({"wall.get"}))

    async def test_catalog_uses_projected_metadata_and_omits_secrets(self):
        result = await connections.connection_catalog(tenant_id=10, actor_id=3)
        self.assertEqual(result["connections"][0]["selection"], {"mode": "user", "credential_id": 7})
        self.assertIn("label", result["connections"][0])
        self.assertIn("state_label", result["connections"][0])
        self.assertEqual(result["connections"][0]["method_scopes"], {"wall.get": ["wall"]})
        self.assertNotIn("secret_encrypted", result["connections"][0])
        self.assertNotIn("refresh_token", str(result))
        self.assertEqual(self.manager.limit_count, 50)
        self.assertNotIn("secret_encrypted", {getattr(col, "name", None) for col in self.manager.selected_columns})


if __name__ == "__main__":
    unittest.main()
