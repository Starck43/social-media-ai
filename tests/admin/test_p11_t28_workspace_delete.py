"""Workspace deletion belongs only to the platform superuser.

Exercise the real access catalog and User permission method bodies with synthetic
role grants. These tests need no database, app startup, or provider access.
"""

import ast
import builtins
from pathlib import Path
from types import SimpleNamespace

import pytest
from source_import_isolation import load_isolated_source

__source_only__ = True

ROOT = Path(__file__).resolve().parents[2]
DB_ENUMS = load_isolated_source(
    "_p11_t28_enum_helpers",
    ROOT / "app/utils/db_enums.py",
    {"app.core.config": SimpleNamespace(settings=SimpleNamespace(DB_SCHEMA="synthetic_no_database"))},
)
TYPES = load_isolated_source("_p11_t28_types", ROOT / "app/types/enums/user_types.py", {"app.utils.db_enums": DB_ENUMS})
CATALOG = load_isolated_source("_p11_t28_access_catalog", ROOT / "app/core/access_catalog.py", {"app.types": TYPES})
ActionType = TYPES.ActionType


def real_user_policy_class():
    """Load the actual User permission methods without constructing ORM metadata."""
    tree = ast.parse((ROOT / "app/models/identity/user.py").read_text())
    user_node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "User")
    names = {"is_superuser", "model_permissions", "has_perm_for", "has_admin_access"}
    methods = [node for node in user_node.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in methods} == names
    node = ast.ClassDef(name="PolicyUser", bases=[], keywords=[], body=methods, decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    normal_import = builtins.__import__

    def imports(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "app.core.access_catalog":
            return CATALOG
        return normal_import(name, globals, locals, fromlist, level)

    namespace = {
        "ActionType": ActionType,
        "PlatformRoleType": TYPES.PlatformRoleType,
        "__builtins__": dict(vars(builtins), __import__=imports),
    }
    exec(compile(module, str(ROOT / "app/models/identity/user.py"), "exec"), namespace)
    return namespace["PolicyUser"]


def synthetic_user(role: str, *, grants=()):
    user = real_user_policy_class()()
    user.is_active = True
    user._model_perms_cache = None
    user.role = SimpleNamespace(
        codename=role,
        permissions=[
            SimpleNamespace(
                action_type=action,
                model_type=SimpleNamespace(resource_key="tenancy.workspace"),
            )
            for action in grants
        ],
    )
    return user


def test_workspace_delete_is_a_declared_catalog_action_and_only_su_can_use_it():
    workspace = CATALOG.resource_for("Tenant")
    assert workspace is not None
    assert workspace.key == "tenancy.workspace"
    assert ActionType.DELETE in workspace.actions
    assert CATALOG.resolve_permission_key("tenancy.workspace.delete") == (workspace, ActionType.DELETE)

    owner = synthetic_user("superuser")
    assert owner.has_perm_for("Tenant", ActionType.DELETE)

    # Even an explicit/stale role grant cannot make destructive workspace
    # deletion available to a platform admin or operator.
    for role in ("admin", "operator"):
        actor = synthetic_user(role, grants=(ActionType.DELETE,))
        assert actor.has_admin_access()
        assert not actor.has_perm_for("Tenant", ActionType.DELETE)

    # Declaring DELETE for this one resource does not make absent actions valid.
    assert not owner.has_perm_for("Tenant", ActionType.EXPORT)
    assert CATALOG.resolve_permission_key("tenancy.workspace.export") is None


@pytest.mark.parametrize("role", ["owner", "admin", "manager", "analyst", "viewer"])
def test_workspace_membership_presets_never_grant_workspace_deletion(role):
    assert ("tenant", ActionType.DELETE) not in CATALOG.workspace_grants(role)
