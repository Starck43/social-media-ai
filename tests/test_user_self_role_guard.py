"""Execute actual manager/admin guard methods with explicit storage doubles."""

import ast
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from source_import_isolation import load_isolated_source
from sqladmin import action
from test_admin_shell_catalog import ROOT, TYPES, actor, source  # noqa: F401 - shared isolated fixture


def manager_source():
    class StorageBoundary:
        def __init__(self, model):
            self.model = model

    fresh = NS(id=7, is_active=True, is_platform_admin=True)
    identities = NS(prefetch_related=Mock(return_value=NS(get=AsyncMock(return_value=fresh))))
    roles = NS(get_by_codename=AsyncMock(return_value=NS(id=128, codename="operator")))
    user_class = NS(objects=identities)
    module = load_isolated_source(
        "app.models.managers._self_role_guard",
        ROOT / "app/models/managers/user_manager.py",
        {
            "passlib.context": NS(CryptContext=lambda **_: NS()),
            "app.core.hashing": NS(get_password_hash=Mock(), verify_password=Mock()),
            "app.models.managers.base_manager": NS(BaseManager=StorageBoundary),
            "app.types": TYPES,
            "app.models.user": NS(User=user_class),
            "app.models": NS(User=user_class, PlatformRole=NS(objects=roles)),
        },
    )
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
    ctx.identities.prefetch_related.return_value.get.return_value = (
        None
        if failure == "deleted"
        else NS(id=7, is_active=failure != "inactive", is_platform_admin=failure != "revoked")
    )
    with pytest.raises(HTTPException) as exc:
        await ctx.manager.set_platform_role(8, TYPES.PlatformRoleType.OPERATOR, actor_id=7)
    assert exc.value.status_code == 403
    ctx.manager.update_by_id.assert_not_awaited()


def user_admin(source):
    tree = ast.parse((ROOT / "app/admin/views.py").read_text())
    node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "UserAdmin")
    ns = {
        "BaseAdmin": source.BaseAdmin,
        "User": source.models.User,
        "PlatformRole": source.PlatformRole,
        "PlatformRoleType": TYPES.PlatformRoleType,
        "ActionType": TYPES.ActionType,
        "action": action,
        "HTTPException": HTTPException,
    }
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), "actual-user-admin", "exec"), ns)
    return ns["UserAdmin"]()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["role", "role_id"])
async def test_generic_admin_crud_cannot_bypass_self_role_guard(source, monkeypatch, field):
    view = user_admin(source)
    writer = AsyncMock()
    monkeypatch.setattr(source.BaseAdmin, "update_model", writer)
    request = NS(state=NS(admin_user=actor()))
    with pytest.raises(HTTPException) as exc:
        await view.update_model(request, "7", {field: 1})
    assert exc.value.status_code == 403
    writer.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_edits_remain_available(source, monkeypatch):
    view = user_admin(source)
    writer = AsyncMock(return_value=NS(username="new-name"))
    monkeypatch.setattr(source.BaseAdmin, "update_model", writer)
    request = NS(state=NS(admin_user=actor()))
    result = await view.update_model(request, "7", {"username": "new-name"})
    assert result.username == "new-name"
    writer.assert_awaited_once()
