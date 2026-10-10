"""Source-isolated WebPerms active-user checks; run directly, no app/DB imports."""

import unittest
from types import SimpleNamespace
import ast
from enum import Enum
from pathlib import Path


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
    root = Path(__file__).resolve().parents[1]
    permissions = ast.parse((root / "app/core/permissions.py").read_text())
    assignment = next(
        node for node in permissions.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "WORKSPACE_OWNER_MODELS" for target in node.targets)
    )
    owner_models = frozenset(ast.literal_eval(assignment.value.args[0]))
    path = root / "app/web/perms.py"
    tree = ast.parse(path.read_text())
    selected = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in {"_resolve_action", "WebPerms"}
    ]
    if {node.name for node in selected} != {"_resolve_action", "WebPerms"}:
        raise RuntimeError("Required WebPerms source definitions absent")
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *selected],
        type_ignores=[],
    )
    scope = {"ActionType": ActionType, "WORKSPACE_OWNER_MODELS": owner_models}
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), scope)
    return scope["WebPerms"]


WebPerms = load_web_perms()


class WebPermsActiveDefaultTests(unittest.TestCase):
    def test_missing_user_denies_access(self):
        perms = WebPerms(user=None, memberships=[], tenant_id=1)
        self.assertFalse(perms.can("source", ActionType.VIEW))
        self.assertFalse(perms.can("source", "view"))

    def test_inactive_user_denies_access_even_if_superuser(self):
        user = SimpleNamespace(is_active=False, is_superuser=True, _is_superuser_role=lambda: True, has_perm_for=lambda m, a: True)
        perms = WebPerms(user=user, memberships=[], tenant_id=1)
        self.assertFalse(perms.can("source", ActionType.VIEW))

    def test_missing_is_active_attribute_defaults_to_false_and_denies(self):
        # Without is_active attribute, getattr(user, "is_active", False) returns False
        user = SimpleNamespace(is_superuser=True, _is_superuser_role=lambda: True, has_perm_for=lambda m, a: True)
        perms = WebPerms(user=user, memberships=[], tenant_id=1)
        self.assertFalse(perms.can("source", ActionType.VIEW))

    def test_missing_or_inactive_owner_and_superuser_grants_are_denied(self):
        for active in ("missing", False):
            for owner, superuser in ((False, False), (True, False), (False, True)):
                with self.subTest(active=active, owner=owner, superuser=superuser):
                    calls = []
                    user = SimpleNamespace(
                        is_superuser=superuser,
                        has_perm_for=lambda model, action: calls.append((model, action)) or True,
                    )
                    if active != "missing":
                        user.is_active = active
                    membership = SimpleNamespace(tenant_id=1, is_owner=owner)
                    perms = WebPerms(user=user, memberships=[membership], tenant_id=1)
                    self.assertFalse(perms.can("source", ActionType.VIEW))
                    self.assertEqual(calls, [])

    def test_active_superuser_still_denies_invalid_model_or_action(self):
        user = SimpleNamespace(is_active=True, is_superuser=True)
        perms = WebPerms(user=user, tenant_id=1)
        for model, action in (("", "view"), (None, "view"), ("source", "unknown")):
            with self.subTest(model=model, action=action):
                self.assertFalse(perms.can(model, action))

    def test_owner_grant_stays_model_and_tenant_bound(self):
        user = SimpleNamespace(is_active=True, is_superuser=False, has_perm_for=lambda model, action: False)
        membership = SimpleNamespace(tenant_id=1, is_owner=True)
        perms = WebPerms(user=user, memberships=[membership], tenant_id=1)
        self.assertFalse(perms.can("job", ActionType.UPDATE))
        perms = WebPerms(user=user, memberships=[membership], tenant_id=2)
        self.assertFalse(perms.can("source", ActionType.UPDATE))

    def test_active_superuser_passes(self):
        user = SimpleNamespace(is_active=True, is_superuser=True, _is_superuser_role=lambda: True)
        perms = WebPerms(user=user, memberships=[], tenant_id=1)
        self.assertTrue(perms.can("source", ActionType.VIEW))

    def test_active_owner_passes_workspace_owner_model(self):
        user = SimpleNamespace(is_active=True, is_superuser=False, _is_superuser_role=lambda: False)
        membership = SimpleNamespace(tenant_id=1, is_owner=True)
        perms = WebPerms(user=user, memberships=[membership], tenant_id=1)
        self.assertTrue(perms.can("source", ActionType.UPDATE))

    def test_active_user_with_permission_passes(self):
        user = SimpleNamespace(
            is_active=True,
            is_superuser=False,
            _is_superuser_role=lambda: False,
            has_perm_for=lambda m, a: m == "source" and a == ActionType.VIEW,
        )
        perms = WebPerms(user=user, memberships=[], tenant_id=1)
        self.assertTrue(perms.can("source", ActionType.VIEW))
        self.assertFalse(perms.can("source", ActionType.UPDATE))


if __name__ == "__main__":
    unittest.main()
