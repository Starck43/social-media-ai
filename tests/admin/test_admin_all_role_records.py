"""Canonical role-manager policy with explicit identity/storage doubles; no DB."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from source_import_isolation import load_isolated_source
from test_admin_shell_catalog import ROOT, TYPES, actor, console, source  # noqa: F401 - shared isolated fixture


def role_manager(source, ctx, target):
    class StorageBoundary:
        def __class_getitem__(cls, _):
            return cls

        def __init__(self, model):
            self.model = model

    module = load_isolated_source(
        "app.models.managers._all_roles_policy",
        ROOT / "app/models/managers/role_manager.py",
        {
            "app.types": TYPES,
            "app.models.managers.base_manager": NS(BaseManager=StorageBoundary),
            "app.core.permissions": source.contexts,
            "app.core.tenant_context": NS(is_bypass=lambda: False),
            **dict.fromkeys(("app.models.identity.role",), NS(role_permission=source.role_module.role_permission)),
            **dict.fromkeys(("app.models.identity.user",), NS(User=source.models.User)),
        },
    )
    manager = module.RoleManager(source.PlatformRole)
    manager.get = AsyncMock(return_value=target)
    manager._replace_secondary = AsyncMock(return_value=2)
    return manager


@pytest.mark.asyncio
@pytest.mark.parametrize("target_role", ["user", "admin", "operator"])
async def test_canonical_writer_accepts_platform_admin_for_every_existing_role(source, target_role):
    ctx = console(source)
    target = source.PlatformRole(id=6, codename=target_role)
    manager = role_manager(source, ctx, target)
    with source.contexts.permission_scope(ctx.user):
        result = await manager.set_permissions(6, [1, 3])
    assert result == 2
    manager._replace_secondary.assert_awaited_once_with(
        source.role_module.role_permission, "role_id", 6, "permission_id", [1, 3]
    )
    assert target.codename == target_role


@pytest.mark.asyncio
@pytest.mark.parametrize("actor_role", ["user", "operator"])
@pytest.mark.parametrize("target_role", ["user", "admin", "operator"])
async def test_canonical_writer_rejects_non_admin_for_every_role(source, actor_role, target_role):
    ctx = console(source, actor(actor_role))
    manager = role_manager(source, ctx, source.PlatformRole(id=6, codename=target_role))
    with source.contexts.permission_scope(ctx.user), pytest.raises(PermissionError):
        await manager.set_permissions(6, [1, 3])
    manager._replace_secondary.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["revoked", "inactive", "deleted", "missing_target"])
async def test_canonical_writer_rechecks_fresh_actor_and_existing_target(source, failure):
    ctx = console(source)
    target = None if failure == "missing_target" else source.PlatformRole(id=6, codename="admin")
    if failure == "deleted":
        fresh = None
    elif failure == "revoked":
        fresh = actor("operator")
    else:
        fresh = actor(active=failure != "inactive")
    source.models.User.objects.prefetch_related.return_value.get.return_value = fresh
    manager = role_manager(source, ctx, target)
    with source.contexts.permission_scope(ctx.user), pytest.raises(PermissionError):
        await manager.set_permissions(6, [1, 3])
    manager._replace_secondary.assert_not_awaited()


@pytest.mark.asyncio
async def test_unbound_interactive_writer_is_still_denied(source):
    ctx = console(source)
    manager = role_manager(source, ctx, source.PlatformRole(id=6, codename="admin"))
    with source.contexts.permission_scope(None), pytest.raises(PermissionError):
        await manager.set_permissions(6, [])
    manager._replace_secondary.assert_not_awaited()


def test_stored_grants_do_not_change_fixed_platform_role_authority():
    ordinary = actor("user")
    ordinary.role.permissions = [NS(action_type=TYPES.ActionType.CONFIGURE)]
    assert not ordinary.has_admin_access()
    assert ordinary.model_permissions("llmmodel") == set()
    administrator = actor("admin")
    administrator.role.permissions = []
    assert administrator.is_platform_admin
    # `admin` reaches the console, but the console is exactly what its role
    # grants. The owner alone gets the wildcard.
    assert administrator.model_permissions("llmmodel") == set()
