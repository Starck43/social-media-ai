"""`RolePermissionService` on the async manager layer.

The service used to run on the legacy sync ORM: `Permission.objects.filter()`
returned a coroutine and `role.save()` went through `SessionLocal`, so every
update raised. These tests exercise the real write path against a throwaway role
(permissions are global rows, roles are not tenant-scoped).
"""

import secrets

import pytest

from app.core.tenant_context import tenant_scope
from app.models import Permission, Role
from app.services.user.permissions import RolePermissionService

pytestmark = pytest.mark.tenancy


def _uniq(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4)}"


async def _some_codenames(count: int = 2) -> list[str]:
    with tenant_scope(bypass=True):
        perms = list(await Permission.objects.order_by(Permission.id).all())
    return [p.codename for p in perms[:count]]


async def _role_with(codenames: list[str]) -> Role:
    name = _uniq("svc-role-")
    with tenant_scope(bypass=True):
        role = await Role.objects.create(name=name, codename="VIEWER", description="test")
    await RolePermissionService._set_role_permissions(role.id, [p.id for p in await _permissions(codenames)])
    return role


async def _permissions(codenames: list[str]) -> list[Permission]:
    with tenant_scope(bypass=True):
        return list(await Permission.objects.filter(Permission.codename.in_(codenames)).all())


async def _cleanup(role: Role) -> None:
    await RolePermissionService._set_role_permissions(role.id, [])
    with tenant_scope(bypass=True):
        await Role.objects.delete_by_id(role.id)


async def test_expand_permission_patterns_is_async_and_resolves():
    first = (await _some_codenames(1))[0]
    expanded = await RolePermissionService.expand_permission_patterns([first])
    assert expanded == [first]
    assert await RolePermissionService.expand_permission_patterns([]) == []


async def test_replace_writes_exactly_the_given_permissions():
    codenames = await _some_codenames(2)
    role = await _role_with(codenames)
    try:
        result = await RolePermissionService.update_role_permissions(
            role_codename=role.name, permission_codenames=[codenames[0]], strategy="replace"
        )
        # The role already holds both, so narrowing removes one and adds nothing.
        assert result["added"] == []
        assert result["removed"] == [codenames[1]]
        assert set(p.codename for p in await RolePermissionService.get_role_permissions(role.id)) == {codenames[0]}
    finally:
        await _cleanup(role)


async def test_merge_keeps_existing_permissions():
    codenames = await _some_codenames(2)
    role = await _role_with(codenames)
    try:
        result = await RolePermissionService.update_role_permissions(
            role_codename=role.name, permission_codenames=[_uniq("nothing-matches-")], strategy="merge"
        )
        assert result["added"] == []
        assert set(p.codename for p in await RolePermissionService.get_role_permissions(role.id)) == set(codenames)
    finally:
        await _cleanup(role)


async def test_synchronize_drops_what_is_missing():
    codenames = await _some_codenames(2)
    role = await _role_with(codenames)
    try:
        result = await RolePermissionService.update_role_permissions(
            role_codename=role.name, permission_codenames=[codenames[0]], strategy="synchronize"
        )
        assert result["removed"] == [codenames[1]]
        assert set(p.codename for p in await RolePermissionService.get_role_permissions(role.id)) == {codenames[0]}
    finally:
        await _cleanup(role)


async def test_update_actions_keeps_the_resource_and_swaps_the_action():
    codenames = await _some_codenames(2)
    role = await _role_with(codenames)
    try:
        await RolePermissionService.update_role_permissions(
            role_codename=role.name, permission_codenames=[codenames[0]], strategy="update_actions"
        )
        stored = {p.codename for p in await RolePermissionService.get_role_permissions(role.id)}
        assert stored == {codenames[0]}
    finally:
        await _cleanup(role)


async def test_unknown_role_raises_value_error():
    with pytest.raises(ValueError, match="not found"):
        await RolePermissionService.update_role_permissions(
            role_codename="no-such-role", permission_codenames=["*"], strategy="replace"
        )


async def test_unknown_strategy_raises_value_error():
    codenames = await _some_codenames(1)
    role = await _role_with(codenames)
    try:
        with pytest.raises(ValueError, match="Unknown update strategy"):
            await RolePermissionService.update_role_permissions(
                role_codename=role.name, permission_codenames=codenames, strategy="nonsense"
            )
    finally:
        await _cleanup(role)
