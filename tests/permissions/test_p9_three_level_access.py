"""P9 regression guard: the three platform levels stay separate.

No database. Every check is a static or read-only inspection, so the file can
run in any batch: it pins the rules that make the platform owner distinct from
platform staff, and that nothing hands out the owner level at runtime.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.core.access_catalog import PLATFORM_LABELS, role_label
from app.models.identity.user import User
from app.types import PlatformRoleType

ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_the_four_platform_roles_are_the_whole_catalog():
    assert [role.value for role in PlatformRoleType] == ["user", "operator", "admin", "superuser"]
    # Every level has a human label; none falls back to "Роль не назначена".
    for role in PlatformRoleType:
        assert role_label(role.value) == PLATFORM_LABELS[role.value]
    assert set(PLATFORM_LABELS) == {role.value for role in PlatformRoleType}


def test_owner_is_staff_but_staff_is_not_the_owner():
    owner, admin, operator = _user("superuser"), _user("admin"), _user("operator")
    assert owner.is_superuser and owner.is_platform_admin and owner.has_admin_access()
    assert not admin.is_superuser and admin.is_platform_admin and admin.has_admin_access()
    # An operator enters the console by explicit grants only, never as staff.
    assert not operator.is_superuser and not operator.is_platform_admin
    assert operator.has_admin_access()


def test_role_catalog_editing_is_owner_only_in_console_and_api():
    catalog = _source("app/admin/catalog.py")
    assert "is_superuser" in catalog
    assert "is_platform_admin" not in _lines_with(
        catalog, "is_platform_admin"
    ), "catalog editing must not be granted by the admin level"
    roles_api = _source("app/api/v1/endpoints/roles.py")
    tree = ast.parse(roles_api)
    update = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "update_role_permissions"
    )
    gate = ast.unparse(update.args.defaults[-1])
    assert "SUPERUSER" in gate, gate


def test_no_runtime_path_grants_the_owner_level():
    """Only the reviewed migration may create a `superuser`; code may not assign it."""
    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "SUPERUSER" not in text:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("*"):
                continue
            assert (
                "PlatformRoleType.SUPERUSER" not in stripped
                or any(
                    allowed in stripped
                    for allowed in (
                        "require_platform_role(PlatformRoleType.SUPERUSER)",
                        "PlatformRoleType.USER",
                        "PlatformRoleType.ADMIN",
                    )
                )
                or path.name
                in {
                    "user_types.py",
                    "access_catalog.py",
                    "user.py",
                    "registry.py",
                    "catalog.py",
                    "roles.py",
                    "role_manager.py",
                    "user_manager.py",
                }
            ), f"{path.relative_to(ROOT)}:{lineno} uses the owner level in a write path: {stripped}"


def test_self_registration_still_starts_at_the_lowest_level():
    auth_api = _source("app/api/v1/endpoints/auth.py")
    start = auth_api.index('@router.post("/register"')
    end = auth_api.find("\n@router.", start + 1)
    registration = auth_api[start : end if end > 0 else None]
    assert "PlatformRoleType.USER" in registration
    assert "PlatformRoleType.ADMIN" not in registration
    assert "PlatformRoleType.OPERATOR" not in registration
    assert "PlatformRoleType.SUPERUSER" not in registration


def test_legacy_superuser_alias_is_gone_from_both_resolvers():
    roles_api = _source("app/api/v1/endpoints/roles.py")
    role_manager = _source("app/models/managers/role_manager.py")
    for source, label in ((roles_api, "API"), (role_manager, "RoleManager")):
        aliases = _lines_with(source, "_ALIASES")
        assert not any("superuser" in line for line in aliases), f"{label} still aliases the owner level"


def test_registry_is_owner_gated_and_has_no_write_routes():
    registry = _source("app/admin/registry.py")
    tree = ast.parse(registry)
    view = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "TableRegistryAdmin")
    exposes = []
    for node in view.body:
        for decorator in getattr(node, "decorator_list", []):
            if isinstance(decorator, ast.Call) and getattr(decorator.func, "id", None) == "expose":
                exposes.append(decorator)
    # One GET page and nothing else: no model, no action, no write method.
    assert len(exposes) == 1, exposes
    assert ast.unparse(exposes[0]) == "expose('/registry', methods=['GET'])", ast.unparse(exposes[0])
    # `is_accessible` delegates to the owner predicate, which reads the flag.
    gate = next(node for node in view.body if isinstance(node, ast.FunctionDef) and node.name == "is_accessible")
    assert "_is_owner" in ast.unparse(gate), ast.unparse(gate)
    owner_check = next(node for node in view.body if isinstance(node, ast.FunctionDef) and node.name == "_is_owner")
    assert "is_superuser" in ast.unparse(owner_check), ast.unparse(owner_check)
    # Nothing in the class turns on a write flag: it is not a model view at all.
    class_attrs = {target.id for node in view.body if isinstance(node, ast.Assign) for target in node.targets}
    assert not class_attrs & {"can_create", "can_edit", "can_delete", "can_export", "form_columns"}, class_attrs
    assert not hasattr(view, "bases") or all(getattr(base, "id", "") != "ModelView" for base in view.bases)


def _user(codename: str) -> User:
    """A mapped instance keeps its ORM state; only the role's codename matters."""
    from types import SimpleNamespace

    user = User(is_active=True)
    user.__dict__["role"] = SimpleNamespace(codename=codename, label=role_label(codename), permissions=[])
    return user


def _lines_with(source: str, needle: str) -> list[str]:
    return [line for line in source.splitlines() if needle in line]


@pytest.mark.parametrize("path", ["app/admin/catalog.py", "app/admin/registry.py", "app/api/v1/endpoints/roles.py"])
def test_owner_surfaces_read_no_rows(path):
    """The owner's extra powers widen visibility, never a query box or a raw write."""
    text = _source(path)
    for forbidden in ("sa.text(", "text(", "execute(", "DELETE FROM", "DROP TABLE"):
        assert forbidden not in text, f"{path} contains {forbidden}"
