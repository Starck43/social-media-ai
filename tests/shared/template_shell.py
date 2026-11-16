"""The shell template contract as one shared test double.

`app/web/templates/web/base.html` (and the partials it includes) reads a fixed
set of attributes from `user` and `perms`. Isolated template tests cannot import
`User`/`WebPerms`, so each fixture used to carry its own `SimpleNamespace`; any
new attribute read by a template broke half the directory at once, which is
exactly the Q-17 debt.

These declarations are the single source of truth for that surface, and
`tests/unit_web/test_q17_shell_contract.py` fails as soon as a template starts
reading an attribute that is not declared here. Defaults are inert and deny
access; pass overrides to grant a capability.
"""

from __future__ import annotations

from types import SimpleNamespace

# Attributes the templates read from the signed-in user.
SHELL_USER_ATTRIBUTES = (
    "id",
    "username",
    "email",
    "role",
    "is_active",
    "is_authenticated",
    "is_platform_admin",
    "has_admin_access",
)

# Attributes the templates read from the request-scoped workspace capabilities.
SHELL_PERMS_ATTRIBUTES = (
    "can",
    "can_open_chat",
    "can_manage_workspace",
    "can_manage_member",
    "is_owner",
    "is_superuser_role",
    "workspace_role",
)


def shell_user(**overrides):
    """A user double that satisfies the shell contract, denying admin by default."""
    values = {
        "id": 101,
        "username": "fixture-user",
        "email": "fixture@fixture.invalid",
        "role": None,
        "is_active": True,
        "is_authenticated": True,
        "is_platform_admin": False,
        "has_admin_access": lambda: False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def shell_perms(**overrides):
    """Capability double with deny-by-default semantics.

    `can` denies every model action and `can_open_chat` is off, so a test that
    wants a privileged surface must pass the grant explicitly.
    """
    values = {
        "can": lambda *args, **kwargs: False,
        "can_open_chat": False,
        "can_manage_workspace": lambda target=None: False,
        "can_manage_member": lambda role=None, target=None: False,
        "is_owner": False,
        "is_superuser_role": False,
        "workspace_role": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)
