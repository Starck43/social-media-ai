"""A deactivated workspace is unreachable through `/app` and `/api`.

The chat identity resolver and invite redemption already refuse an inactive
tenant; web and machine API access must fail closed the same way even while the
membership row itself stays active.
"""

from __future__ import annotations

import re
import secrets

from httpx import ASGITransport, AsyncClient

from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import Role, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.types import PlatformRoleType, WorkspaceRoleType

PASSWORD = "secret-password-1"


async def _client() -> AsyncClient:
    app = create_application()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True)


async def _csrf(client: AsyncClient, path: str) -> str:
    page = await client.get(path)
    assert page.status_code == 200
    match = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert match, f"no csrf input on {path}"
    return match.group(1)


async def _register(client: AsyncClient, prefix: str) -> tuple[User, int]:
    """Sign up through the web UI: the new user owns a fresh workspace and is logged in."""
    username = f"{prefix}{secrets.token_hex(3)}"
    token = await _csrf(client, "/app/register")
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
    memberships = await TenantUserManager().web_memberships(user.id)
    assert user is not None and memberships
    return user, memberships[0].tenant_id


async def _bearer(client: AsyncClient, username: str) -> dict[str, str]:
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": username, "password": PASSWORD},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _drop(users: list[User], tenant_ids: list[int]) -> None:
    for user in users:
        await User.objects.delete_user(user.id)
    with tenant_scope(bypass=True):
        for tenant_id in tenant_ids:
            await tenants.delete_by_id(tenant_id)


async def test_web_memberships_skip_inactive_workspaces() -> None:
    async with await _client() as c:
        member, own_tenant = await _register(c, "InactMember")
    async with await _client() as c:
        host, host_tenant = await _register(c, "InactHost")
    try:
        viewer = await Role.objects.get(codename=PlatformRoleType.USER.value)
        await TenantUserManager().add_web_member(tenant_id=host_tenant, user_id=member.id, role_id=viewer.id)
        assert {m.tenant_id for m in await TenantUserManager().web_memberships(member.id)} == {own_tenant, host_tenant}

        await tenants.update_by_id(host_tenant, is_active=False)

        assert [m.tenant_id for m in await TenantUserManager().web_memberships(member.id)] == [own_tenant]
        assert await TenantUserManager().web_memberships(host.id) == []
    finally:
        await _drop([member, host], [own_tenant, host_tenant])


async def test_deactivated_workspace_blocks_web_and_api_access() -> None:
    async with await _client() as c:
        owner, tenant_id = await _register(c, "InactOwner")
        try:
            headers = await _bearer(c, owner.username)
            assert (await c.get("/api/v1/users/me", headers=headers)).status_code == 200

            await tenants.update_by_id(tenant_id, is_active=False)

            page = await c.get("/app/sources")
            assert page.url.path == "/app/invite"
            resp = await c.get("/api/v1/users/me", headers=headers)
            assert resp.status_code == 403
            assert resp.json()["detail"] == "User has no workspace membership"
        finally:
            await _drop([owner], [tenant_id])


async def test_api_tenant_header_cannot_select_a_deactivated_workspace() -> None:
    async with await _client() as c:
        member, own_tenant = await _register(c, "InactHdrMember")
    async with await _client() as c:
        host, host_tenant = await _register(c, "InactHdrHost")
    try:
        viewer = await Role.objects.get(codename=PlatformRoleType.USER.value)
        await TenantUserManager().add_web_member(tenant_id=host_tenant, user_id=member.id, role_id=viewer.id)
        await tenants.update_by_id(host_tenant, is_active=False)

        async with await _client() as c:
            headers = await _bearer(c, member.username)
            refused = await c.get("/api/v1/users/me", headers={**headers, "X-Tenant-Id": str(host_tenant)})
            assert refused.status_code == 403
            assert refused.json()["detail"] == "User is not a member of this workspace"
            own = await c.get("/api/v1/users/me", headers={**headers, "X-Tenant-Id": str(own_tenant)})
            assert own.status_code == 200
    finally:
        await _drop([member, host], [own_tenant, host_tenant])
