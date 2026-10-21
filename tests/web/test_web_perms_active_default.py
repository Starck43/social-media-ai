"""Source-isolated WebPerms active-user checks; run directly, no app/DB imports."""

import ast
import unittest
from enum import Enum
from pathlib import Path
from types import SimpleNamespace


class ActionType(Enum):
    """Minimal action double; this package tests active-user gating, not enums."""

    VIEW = "view"
    UPDATE = "update"

    @classmethod
    def get_by_value(cls, value):
        return next((member for member in cls if member.value == value), None)

    @classmethod
    def get_by_name(cls, name):
        return cls.__members__.get(name)


def load_web_perms():
    root = Path(__file__).resolve().parents[2]
    permissions = ast.parse((root / "app/core/permissions.py").read_text())
    assignment = next(
        node
        for node in permissions.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "WORKSPACE_OWNER_MODELS" for target in node.targets)
    )
    owner_models = frozenset(ast.literal_eval(assignment.value.args[0]))
    path = root / "app/web/perms.py"
    tree = ast.parse(path.read_text())
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in {"_resolve_action", "WebPerms"}
    ]
    if {node.name for node in selected} != {"_resolve_action", "WebPerms"}:
        raise RuntimeError("Required WebPerms source definitions absent")
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *selected],
        type_ignores=[],
    )
    resource_for = lambda key: (
        SimpleNamespace(model_name=str(key).lower(), workspace=key in {"source", "agenttask"}, actions=set(ActionType))
        if key in {"source", "agenttask", "job"}
        else None
    )

    class RewriteCatalogImport(ast.NodeTransformer):
        def visit_ImportFrom(self, node):
            if node.module == "app.core.access_catalog" and any(alias.name == "resource_for" for alias in node.names):
                return ast.Assign(
                    targets=[ast.Name(id="resource_for", ctx=ast.Store())],
                    value=ast.Name(id="_resource_for", ctx=ast.Load()),
                )
            return node

    module = RewriteCatalogImport().visit(module)
    scope = {"ActionType": ActionType, "WORKSPACE_OWNER_MODELS": owner_models, "_resource_for": resource_for}
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), scope)
    return scope["WebPerms"]


WebPerms = load_web_perms()


class WebPermsActiveDefaultTests(unittest.TestCase):
    def test_missing_user_denies_access(self):
        perms = WebPerms(user=None, memberships=[], tenant_id=1)
        self.assertFalse(perms.can("source", ActionType.VIEW))
        self.assertFalse(perms.can("source", "view"))

    def test_inactive_user_denies_access_even_if_platform_admin(self):
        user = SimpleNamespace(is_active=False, is_platform_admin=True, has_perm_for=lambda m, a: True)
        perms = WebPerms(user=user, memberships=[], tenant_id=1)
        self.assertFalse(perms.can("source", ActionType.VIEW))

    def test_missing_is_active_attribute_defaults_to_false_and_denies(self):
        # Without is_active attribute, getattr(user, "is_active", False) returns False
        user = SimpleNamespace(is_platform_admin=True, has_perm_for=lambda m, a: True)
        perms = WebPerms(user=user, memberships=[], tenant_id=1)
        self.assertFalse(perms.can("source", ActionType.VIEW))

    def test_missing_or_inactive_owner_and_platform_admin_are_denied(self):
        for active in ("missing", False):
            for owner, platform_admin in ((False, False), (True, False), (False, True)):
                with self.subTest(active=active, owner=owner, platform_admin=platform_admin):
                    calls = []
                    user = SimpleNamespace(
                        id=5,
                        is_platform_admin=platform_admin,
                        has_perm_for=lambda model, action: calls.append((model, action)) or True,
                    )
                    if active != "missing":
                        user.is_active = active
                    membership = SimpleNamespace(
                        tenant_id=1,
                        user_id=5,
                        channel="web",
                        is_active=True,
                        role=SimpleNamespace(codename="owner" if owner else "viewer"),
                        is_owner=owner,
                    )
                    perms = WebPerms(user=user, memberships=[membership], tenant_id=1, member_grants=frozenset())
                    self.assertFalse(perms.can("source", ActionType.VIEW))
                    self.assertEqual(calls, [])

    def test_active_platform_admin_still_denies_invalid_model_or_action(self):
        user = SimpleNamespace(is_active=True, is_platform_admin=True, id=5)
        perms = WebPerms(user=user, tenant_id=1)
        for model, action in (("", "view"), (None, "view"), ("source", "unknown")):
            with self.subTest(model=model, action=action):
                self.assertFalse(perms.can(model, action))

    def test_owner_grant_stays_model_and_tenant_bound(self):
        user = SimpleNamespace(is_active=True, is_platform_admin=False, id=5, has_perm_for=lambda model, action: False)
        membership = SimpleNamespace(
            tenant_id=1, user_id=5, channel="web", is_active=True, role=SimpleNamespace(codename="owner"), is_owner=True
        )
        perms = WebPerms(user=user, memberships=[membership], tenant_id=1, member_grants=frozenset())
        self.assertFalse(perms.can("job", ActionType.UPDATE))
        perms = WebPerms(
            user=user, memberships=[membership], tenant_id=2, member_grants=frozenset({("source", ActionType.UPDATE)})
        )
        self.assertFalse(perms.can("source", ActionType.UPDATE))

    def test_active_platform_admin_does_not_bypass_workspace_membership(self):
        user = SimpleNamespace(is_active=True, is_platform_admin=True, id=5)
        perms = WebPerms(user=user, memberships=[], tenant_id=1)
        self.assertFalse(perms.can("source", ActionType.VIEW))

    def test_active_owner_passes_workspace_owner_model(self):
        user = SimpleNamespace(is_active=True, is_platform_admin=False, id=5)
        membership = SimpleNamespace(
            tenant_id=1, user_id=5, channel="web", is_active=True, role=SimpleNamespace(codename="owner"), is_owner=True
        )
        perms = WebPerms(
            user=user, memberships=[membership], tenant_id=1, member_grants=frozenset({("source", ActionType.UPDATE)})
        )
        self.assertTrue(perms.can("source", ActionType.UPDATE))

    def test_active_user_with_permission_passes(self):
        user = SimpleNamespace(
            is_active=True,
            is_platform_admin=False,
            id=5,
            has_perm_for=lambda m, a: m == "source" and a == ActionType.VIEW,
        )
        membership = SimpleNamespace(
            tenant_id=1,
            user_id=5,
            channel="web",
            is_active=True,
            role=SimpleNamespace(codename="viewer"),
            is_owner=False,
        )
        perms = WebPerms(
            user=user, memberships=[membership], tenant_id=1, member_grants=frozenset({("source", ActionType.VIEW)})
        )
        self.assertTrue(perms.can("source", ActionType.VIEW))
        self.assertFalse(perms.can("source", ActionType.UPDATE))


if __name__ == "__main__":
    unittest.main()
