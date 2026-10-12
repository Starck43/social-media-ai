"""A non-owner member's workspace role decides their rights in that workspace.

The platform role (`users.role_id`) still governs global models and the
operator console; inside a workspace, the role the owner gave the membership
(`tenant_users.role_id`, the same `Role` matrix) applies to workspace-owned
models in the web UI, the API and agent tools alike.
"""

from __future__ import annotations

import re
import secrets
from types import SimpleNamespace

from httpx import ASGITransport, AsyncClient

from app.core.permissions import has_permission, permission_scope, workspace_models
from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import Platform, Role, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.types import ActionType, UserRoleType
from app.web.perms import WebPerms

PASSWORD = "secret-password-1"


def _fake_user(platform_grants: set[tuple[str, ActionType]], *, superuser: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=42,
        is_active=True,
        is_superuser=superuser,
        _is_superuser_role=lambda: superuser,
        has_perm_for=lambda model, action: superuser or (model, action) in platform_grants,
    )


def test_workspace_models_are_the_tenant_scoped_ones() -> None:
    names = workspace_models()
    assert {"source", "agenttask", "agentscenario", "aianalytics", "digestrun"} <= names
    assert not {"llmmodel", "llmprovider", "platform", "user", "role", "tenant"} & names


def test_member_role_replaces_platform_role_only_for_workspace_models() -> None:
    admin_like = {("source", ActionType.CREATE), ("llmmodel", ActionType.CREATE)}
    user = _fake_user(admin_like)
    viewer_grants = frozenset({("source", ActionType.VIEW), ("llmmodel", ActionType.CREATE)})
    with tenant_scope(31), permission_scope(user, member_grants=viewer_grants):
        assert has_permission(user, "source", "view")
        assert not has_permission(user, "source", "create")  # platform ADMIN does not leak in
        assert has_permission(user, "llmmodel", "create")  # global model: platform role
    # Another workspace is not restricted by this scope's grants.
    with tenant_scope(31), permission_scope(user, member_grants=viewer_grants), tenant_scope(32):
        assert has_permission(user, "source", "create")


def test_member_role_can_grant_more_than_the_platform_role_inside_the_workspace() -> None:
    user = _fake_user(set())
    with tenant_scope(31), permission_scope(user, member_grants=frozenset({("agentscenario", ActionType.UPDATE)})):
        assert has_permission(user, "agentscenario", "update")
        assert not has_permission(user, "agentscenario", "delete")
        assert not has_permission(user, "llmmodel", "update")


def test_owner_and_platform_superuser_are_unchanged() -> None:
    user = _fake_user(set())
    with tenant_scope(31), permission_scope(user, is_owner=True, member_grants=frozenset()):
        assert has_permission(user, "source", "delete")
    root = _fake_user(set(), superuser=True)
    with tenant_scope(31), permission_scope(root, member_grants=frozenset()):
        assert has_permission(root, "source", "delete")


def test_empty_member_grants_fail_closed_on_workspace_models() -> None:
    user = _fake_user({("source", ActionType.VIEW)})
    with tenant_scope(31), permission_scope(user, member_grants=frozenset()):
        assert not has_permission(user, "source", "view")


def test_web_perms_follow_the_member_role() -> None:
    user = _fake_user({("source", ActionType.CREATE)})
    member = SimpleNamespace(tenant_id=31, is_owner=False, role=None)
    viewer = frozenset({("source", ActionType.VIEW)})
    perms = WebPerms(user, [member], 31, viewer)
    assert perms.can("source", "view")
    assert not perms.can("source", "create")
    owner = SimpleNamespace(tenant_id=31, is_owner=True, role=None)
    assert WebPerms(user, [owner], 31, None).can("source", "delete")
    # Without grants (legacy callers) the platform role still answers.
    assert WebPerms(user, [member], 31).can("source", "create")


# ── API, end to end ─────────────────────────────────────────────────────────


async def _client() -> AsyncClient:
    app = create_application()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True)


async def _register(client: AsyncClient, prefix: str) -> tuple[User, int]:
    username = f"{prefix}{secrets.token_hex(3)}"
    page = await client.get("/app/register")
    token = re.search(r'name="_csrf" value="([^"]+)"', page.text).group(1)
    resp = await client.post(
        "/app/register",
        data={
            "username": username,
            "email": f"{username}@example.com",
            "password": PASSWORD,
            "workspace": f"{prefix} workspace",
            "_csrf": token,
        },
    )
    assert resp.status_code == 200
    user = await User.objects.get(username=username)
    return user, (await TenantUserManager().web_memberships(user.id))[0].tenant_id


async def _bearer(client: AsyncClient, username: str) -> dict[str, str]:
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": username, "password": PASSWORD},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _source_payload() -> dict:
    platform = await Platform.objects.filter(platform_type="telegram").first()
    return {
        "name": "Member role source",
        "platform_id": platform.id,
        "source_type": "USER",
        "external_id": secrets.token_hex(6),
    }


async def _drop(users: list[User], tenant_ids: list[int]) -> None:
    for user in users:
        await User.objects.delete_user(user.id)
    with tenant_scope(bypass=True):
        for tenant_id in tenant_ids:
            await tenants.delete_by_id(tenant_id)


async def _member_of_host(platform_role: UserRoleType, member_role: UserRoleType):
    async with await _client() as c:
        member, own_tenant = await _register(c, "RoleMember")
    async with await _client() as c:
        host, host_tenant = await _register(c, "RoleHost")
    platform = await Role.objects.get(codename=platform_role.name)
    await User.objects.update_by_id(member.id, role_id=platform.id)
    workspace_role = await Role.objects.get(codename=member_role.name)
    await TenantUserManager().add_web_member(tenant_id=host_tenant, user_id=member.id, role_id=workspace_role.id)
    return member, host, own_tenant, host_tenant


async def test_platform_admin_invited_as_viewer_cannot_write_in_the_host_workspace() -> None:
    member, host, own_tenant, host_tenant = await _member_of_host(UserRoleType.ADMIN, UserRoleType.VIEWER)
    try:
        async with await _client() as c:
            headers = {**await _bearer(c, member.username), "X-Tenant-Id": str(host_tenant)}
            assert (await c.get("/api/v1/sources", headers=headers)).status_code == 200
            created = await c.post("/api/v1/sources", json=await _source_payload(), headers=headers)
            assert created.status_code == 403
            assert created.json()["detail"] == "Missing permission: source.create"
    finally:
        await _drop([member, host], [own_tenant, host_tenant])


async def test_member_role_grants_workspace_writes_beyond_the_platform_role() -> None:
    member, host, own_tenant, host_tenant = await _member_of_host(UserRoleType.VIEWER, UserRoleType.MANAGER)
    try:
        async with await _client() as c:
            headers = {**await _bearer(c, member.username), "X-Tenant-Id": str(host_tenant)}
            created = await c.post("/api/v1/sources", json=await _source_payload(), headers=headers)
            assert created.status_code == 201, created.text
            # The member's own workspace still follows the platform role (owner there).
            own = {**headers, "X-Tenant-Id": str(own_tenant)}
            assert (await c.get("/api/v1/sources", headers=own)).status_code == 200
    finally:
        await _drop([member, host], [own_tenant, host_tenant])
