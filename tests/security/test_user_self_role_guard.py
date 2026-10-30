"""Execute actual manager/admin guard methods with explicit storage doubles."""

import ast
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from source_import_isolation import load_isolated_source
from sqladmin import action


def _load_dep(name, path):
    # Cross-group helper module loader (absolute path; bare sibling imports do not
    # resolve across domain dirs).
    import pathlib

    filepath = pathlib.Path(__file__).resolve().parents[2] / path
    spec = importlib.util.spec_from_file_location(name, filepath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    # Same insertion pytest itself does for collected test modules so the loaded
    # helper's own bare sibling imports keep resolving.
    if str(filepath.parent) not in sys.path:
        sys.path.insert(0, str(filepath.parent))
    spec.loader.exec_module(module)
    return module


_catalog_shared = _load_dep("_dep_admin_shell_catalog", "tests/admin/test_admin_shell_catalog.py")
ROOT, TYPES, actor, source = _catalog_shared.ROOT, _catalog_shared.TYPES, _catalog_shared.actor, _catalog_shared.source


def manager_source():
    class StorageBoundary:
        def __init__(self, model):
            self.model = model

    fresh = NS(id=7, is_active=True, is_platform_admin=True)
    fresh.has_admin_access = lambda: bool(fresh.is_active and fresh.is_platform_admin)
    # Rank decides the assignment, so the actor needs the role it claims.
    fresh.role = NS(codename=TYPES.PlatformRoleType.ADMIN.value)
    identities = NS()
    roles = NS(get_by_codename=AsyncMock(return_value=NS(id=128, codename="operator")))
    # The target row the manager re-reads through the same storage before it
    # writes the new role; it must sit below the actor for the rank rule.
    target = NS(
        id=8,
        is_active=True,
        is_superuser=False,
        is_platform_admin=False,
        role=NS(codename=TYPES.PlatformRoleType.USER.value),
    )
    target.has_admin_access = lambda: False

    # A later assignment can point the actor lookup at another identity (revoked,
    # inactive, deleted), so the rows are resolved per requested id, not by call order.
    def _row(**kwargs):
        return fresh if kwargs.get("id") == fresh.id else target

    # One `get` double for both lookups: tests can point either row at another
    # identity (revoked/inactive/deleted) through `identities.get`.
    identities.get = AsyncMock(side_effect=_row)
    identities.prefetch_related = Mock(return_value=identities)

    user_class = NS(objects=identities)
    # `set_platform_role` imports the identity class as `PlatformUser` so the
    # type-checked forward reference cannot shadow the class lookup.
    module = load_isolated_source(
        "app.models.managers._self_role_guard",
        ROOT / "app/models/managers/user_manager.py",
        {
            "passlib.context": NS(CryptContext=lambda **_: NS()),
            "app.core.hashing": NS(get_password_hash=Mock(), verify_password=Mock()),
            "app.models.managers.base_manager": NS(BaseManager=StorageBoundary),
            "app.types": TYPES,
            **dict.fromkeys(("app.models.identity.user",), NS(User=user_class)),
            "app.models": NS(User=user_class, PlatformRole=NS(objects=roles)),
        },
    )
    # The source resolves the identity class through the declared import double.
    module.PlatformUser = user_class
    manager = module.UserManager()
    manager.get = AsyncMock(return_value=NS(id=8))
    manager.update_by_id = AsyncMock(return_value=NS(id=8, role_id=128))
    return NS(manager=manager, identities=identities, roles=roles)


@pytest.mark.asyncio
@pytest.mark.parametrize("new_role", list(TYPES.PlatformRoleType))
async def test_platform_admin_cannot_assign_own_role_even_to_admin(new_role):
    ctx = manager_source()
    with pytest.raises(HTTPException) as exc:
        await ctx.manager.set_platform_role(7, new_role, actor_id=7)
    assert exc.value.status_code == 403
    ctx.roles.get_by_codename.assert_not_awaited()
    ctx.manager.update_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_can_still_assign_another_users_role():
    ctx = manager_source()
    await ctx.manager.set_platform_role(8, TYPES.PlatformRoleType.OPERATOR, actor_id=7)
    ctx.manager.update_by_id.assert_awaited_once_with(8, role_id=128)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["revoked", "inactive", "deleted"])
async def test_role_assignment_still_rechecks_authority(failure):
    ctx = manager_source()
    ctx.identities.get.side_effect = None
    ctx.identities.get.return_value = (
        None
        if failure == "deleted"
        else NS(
            id=7,
            is_active=failure != "inactive",
            is_platform_admin=failure != "revoked",
            is_superuser=False,
            has_admin_access=lambda: failure not in ("revoked", "inactive"),
            role=NS(codename=TYPES.PlatformRoleType.ADMIN.value),
        )
    )
    with pytest.raises(HTTPException) as exc:
        await ctx.manager.set_platform_role(8, TYPES.PlatformRoleType.OPERATOR, actor_id=7)
    assert exc.value.status_code == 403
    ctx.manager.update_by_id.assert_not_awaited()


def user_admin(source, monkeypatch, manager=None, admin=None):
    """The real `UserAdmin` with its storage reads pointed at one target row.

    `update_model` re-reads the target through the *application* `User.objects`
    and hands the payload to the manager, so both boundaries are doubled here
    instead of reaching a database. The actor is the caller's own double, so
    the extracted class compares it by `id` like production does.
    """
    manager = manager or manager_source()
    admin = admin or NS(
        id=7,
        username="verified-admin",
        email="admin@example.invalid",
        is_active=True,
        role=NS(codename=TYPES.PlatformRoleType.ADMIN.value),
    )
    # `/admin` talks to the *application* `User` (the class the API uses),
    # whose `objects` is the live manager — never the extracted double. The row
    # reads AND the role write are stubbed on that manager, through
    # `monkeypatch` so the process is left without a double.
    from app.models import User as RuntimeUser

    monkeypatch.setattr(RuntimeUser.objects, "prefetch_related", lambda *_: NS(get=AsyncMock(return_value=admin)))
    monkeypatch.setattr(RuntimeUser, "objects", RuntimeUser.objects)
    # The extracted manager resolves both the target and its role write through
    # the `User` class it was handed, so the write seam lives on that resolver
    # too — and `prefetch_related` has to answer with the same row.
    row_read = Mock(return_value=manager.identities)
    manager.identities.prefetch_related = row_read
    manager.identities.set_platform_role = manager.manager.set_platform_role

    tree = ast.parse((ROOT / "app/admin/view_modules/users.py").read_text())
    node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "UserAdmin")
    from app.admin.role_picker import RoleChoiceField, RolePickerConverter, role_choices, role_edit
    from app.models.managers.user_manager import UserManager as _UserManager

    ns = {
        "BaseAdmin": source.BaseAdmin,
        "User": RuntimeUser,
        "PlatformRole": source.PlatformRole,
        "PlatformRoleType": TYPES.PlatformRoleType,
        "ActionType": TYPES.ActionType,
        "action": action,
        "HTTPException": HTTPException,
        # The class body names these directly (`form_overrides`, `form_converter`
        # and the edit-form hook); production imports them at module level.
        "RoleChoiceField": RoleChoiceField,
        "RolePickerConverter": RolePickerConverter,
        "role_choices": role_choices,
        "role_edit": role_edit,
        # `update_model` calls `UserManager._is_platform_owner` by name; it is a
        # static guard that only reads `role.codename`, so the module under test
        # is imported for real rather than re-stubbed.
        "UserManager": _UserManager,
    }
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), "actual-user-admin", "exec"), ns)
    return ns["UserAdmin"]()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,status", [({"role": "user"}, 403), ({"role_id": 1}, 400)])
async def test_generic_admin_crud_cannot_bypass_self_role_guard(source, monkeypatch, payload, status):
    """A hand-crafted role payload never reaches the generic writer.

    Two separate answers, both refusals: a `role` name is routed through the
    manager, which refuses the self-edit with 403; a raw `role_id` is not a
    field the console accepts at all and is answered with 400, pointing at the
    dedicated operation. Neither may call `BaseAdmin.update_model` or the role
    write.
    """
    manager = manager_source()
    # Controls: the actor must be recognizable to the owner guard
    # (`_is_platform_owner` reads `role.codename` and the active flag).
    admin = NS(
        id=7,
        username="verified-admin",
        email="admin@example.invalid",
        is_active=True,
        role=NS(codename=TYPES.PlatformRoleType.ADMIN.value),
    )
    admin.has_admin_access = lambda: True
    # `set_platform_role` goes through the same resolver the manager reads rows
    # from, so the role write is observed on that seam.
    role_write = AsyncMock(return_value=NS(id=7))
    manager.identities.set_platform_role = role_write
    view = user_admin(source, monkeypatch, manager, admin)
    writer = AsyncMock()
    monkeypatch.setattr(source.BaseAdmin, "update_model", writer)
    request = NS(state=NS(admin_user=admin))

    with pytest.raises(HTTPException) as exc:
        await view.update_model(request, "7", payload)
    assert exc.value.status_code == status
    writer.assert_not_awaited()
    manager.manager.update_by_id.assert_not_awaited()
    role_write.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_edits_remain_available(source, monkeypatch):
    view = user_admin(source, monkeypatch)
    writer = AsyncMock(return_value=NS(username="new-name"))
    monkeypatch.setattr(source.BaseAdmin, "update_model", writer)
    request = NS(state=NS(admin_user=actor()))
    result = await view.update_model(request, "7", {"username": "new-name"})
    assert result.username == "new-name"
    writer.assert_awaited_once()
