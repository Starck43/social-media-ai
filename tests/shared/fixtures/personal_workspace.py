"""Owned global users and active/invited workspace memberships; no reference edits."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.permissions import operator_permission_scope
from app.core.tenant_context import tenant_scope
from app.models import PlatformRole, Tenant, TenantUser, User, WorkspaceRole


@pytest.fixture
async def personal_world():
    tag = "p246-" + uuid4().hex[:12]
    users, tenants, memberships = [], [], []
    try:
        with tenant_scope(bypass=True), operator_permission_scope():
            platform_role = await PlatformRole.objects.get(codename="user")
            roles = {name: await WorkspaceRole.objects.get(codename=name) for name in ("owner", "manager", "viewer")}
            for i in range(4):
                users.append(
                    await User.objects.create(
                        username=f"{tag}-{i}",
                        email=f"{tag}-{i}@example.invalid",
                        hashed_password="synthetic-not-a-password",
                        role_id=platform_role.id,
                        is_active=True,
                    )
                )
            for i in range(2):
                tenants.append(await Tenant.objects.create(slug=f"{tag}-{i}", name=f"synthetic {tag} {i}"))
            for ui, ti, role in ((0, 0, "owner"), (0, 1, "manager"), (1, 0, "owner"), (2, 1, "owner")):
                memberships.append(
                    await TenantUser.objects.create(
                        tenant_id=tenants[ti].id,
                        user_id=users[ui].id,
                        channel="web",
                        external_user_id=str(users[ui].id),
                        role_id=roles[role].id,
                        is_active=True,
                    )
                )
            users = [await User.objects.prefetch_related("role.permissions").get(id=u.id) for u in users]
        yield SimpleNamespace(users=users, tenants=tenants, memberships=memberships, roles=roles, tag=tag)
    finally:
        with tenant_scope(bypass=True), operator_permission_scope():
            for tenant in tenants:
                await Tenant.objects.delete_by_id(tenant.id)
            for user in users:
                await User.objects.delete_by_id(user.id)
