"""Tests for WebPerms.can fail-closed is_active handling."""

import unittest
from types import SimpleNamespace
from app.types import ActionType
from app.web.perms import WebPerms


class WebPermsActiveDefaultTests(unittest.TestCase):
    def test_missing_user_denies_access(self):
        perms = WebPerms(user=None, memberships=[], tenant_id=1)
        self.assertFalse(perms.can("source", ActionType.VIEW))
        self.assertFalse(perms.can("source", "view"))

    def test_inactive_user_denies_access_even_if_superuser(self):
        user = SimpleNamespace(is_active=False, is_superuser=True, _is_superuser_role=lambda: True, has_perm_for=lambda m, a: True)
        perms = WebPerms(user=user, memberships=[], tenant_id=1)
        self.assertFalse(perms.can("source", ActionType.VIEW))

    def tfest_missing_is_active_attribute_defaults_to_false_and_denies(self):
        # Without is_active attribute, getattr(user, "is_active", False) returns False
        user = SimpleNamespace(is_superuser=True, _is_superuser_role=lambda: True, has_perm_for=lambda m, a: True)
        perms = WebPerms(user=user, memberships=[], tenant_id=1)
        self.assertFalse(perms.can("source", ActionType.VIEW))

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
