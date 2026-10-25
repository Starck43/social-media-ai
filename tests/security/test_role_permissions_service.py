"""Structured platform grants are configurable only for the operator role."""

from contextlib import asynccontextmanager

import pytest

from app.models import Permission, PlatformRole
from app.services.user.permissions import RolePermissionService
from app.types import PlatformRoleType

pytestmark = pytest.mark.tenancy


async def _some_codenames(count: int = 3) -> list[str]:
    permissions = await Permission.objects.order_by(Permission.id).all()
    return [permission.codename for permission in permissions[:count]]


@asynccontextmanager
async def _operator_with(codenames: list[str]):
    role = await PlatformRole.objects.get_by_codename(PlatformRoleType.OPERATOR)
    original = await RolePermissionService.get_role_permissions(role.id)
    permissions = [await Permission.objects.get_by_codename(code) for code in codenames]
    assert all(permissions), "requested permission keys are seeded"
    await RolePermissionService._set_role_permissions(role.id, [permission.id for permission in permissions])
    try:
        yield role
    finally:
        await RolePermissionService._set_role_permissions(role.id, [permission.id for permission in original])


async def test_expand_resolves_only_registered_permission_keys():
    key = (await _some_codenames(1))[0]
    assert await RolePermissionService.expand_permission_patterns([key]) == [key]
    assert await RolePermissionService.expand_permission_patterns([]) == []
    with pytest.raises(ValueError, match="Unknown permission"):
        await RolePermissionService.expand_permission_patterns(["*"])


async def test_replace_updates_only_operator_grants():
    first, second = await _some_codenames(2)
    async with _operator_with([first, second]) as operator:
        result = await RolePermissionService.update_role_permissions(
            role_codename=operator.codename, permission_codenames=[first], strategy="replace"
        )
        assert result["added"] == []
        assert result["removed"] == [second]
        assert {p.codename for p in await RolePermissionService.get_role_permissions(operator.id)} == {first}


async def test_merge_keeps_existing_operator_grants_and_adds_known_keys():
    first, second, third = await _some_codenames(3)
    async with _operator_with([first, second]) as operator:
        result = await RolePermissionService.update_role_permissions(
            role_codename=operator.codename, permission_codenames=[third], strategy="merge"
        )
        assert result["added"] == [third]
        assert {p.codename for p in await RolePermissionService.get_role_permissions(operator.id)} == {
            first,
            second,
            third,
        }


async def test_synchronize_replaces_operator_grants():
    first, second = await _some_codenames(2)
    async with _operator_with([first, second]) as operator:
        result = await RolePermissionService.update_role_permissions(
            role_codename=operator.codename, permission_codenames=[first], strategy="synchronize"
        )
        assert result["removed"] == [second]
        assert {p.codename for p in await RolePermissionService.get_role_permissions(operator.id)} == {first}


async def test_non_operator_roles_and_unknown_strategies_are_rejected():
    with pytest.raises(ValueError, match="Only operator grants"):
        await RolePermissionService.update_role_permissions(
            role_codename=PlatformRoleType.USER.value,
            permission_codenames=["collection.source.view"],
            strategy="replace",
        )

    with pytest.raises(ValueError, match="Unknown update strategy"):
        await RolePermissionService.update_role_permissions(
            role_codename=PlatformRoleType.OPERATOR.value,
            permission_codenames=[],
            strategy="update_actions",
        )
