"""API authentication and workspace isolation (`ApiScopeMiddleware`).

The HTTP surfaces are split in two: `/api/*` is a client surface — authenticated
and pinned to a workspace the caller is an active member of — while `/admin/*`
and the CLI are the operator console and deliberately bypass the tenant guard.
These tests pin the client half of that split: no token means no data, a member
never reads another workspace's rows, and the `X-Tenant-*` headers can only
narrow the caller's access, never widen it.
"""

import secrets

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import Platform, Role, Source, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.types.enums.platform_types import SourceType
from app.types.enums.user_types import UserRoleType
from app.utils.token import create_access_token

pytestmark = pytest.mark.tenancy  # no platform-owner bypass: the guard must work

SOURCES_URL = "/api/v1/dashboard/sources"


def _uniq(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4)}"


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=create_application()), base_url="http://testserver")


async def _member() -> tuple[int, str]:
    """A workspace, an active web member of it, and a bearer token for them."""
    username = _uniq("api")
    role = await Role.objects.get(codename=UserRoleType.VIEWER.name)
    user = await User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="secret-password-1",
        role_id=role.id,
    )
    tenant = await tenants.create(name=_uniq("WS "), slug=_uniq("ws-"))
    await TenantUserManager().add_web_member(tenant_id=tenant.id, user_id=user.id, role="owner")
    return tenant.id, create_access_token(subject=str(user.id))[0]


async def _add_source(tenant_id: int, name: str) -> int:
    platforms = await Platform.objects.all()
    assert platforms, "no platform rows in the test database"
    with tenant_scope(tenant_id):
        source = await Source.objects.create(
            platform_id=platforms[0].id,
            name=name,
            source_type=SourceType.PUBLIC,
            external_id=_uniq("ext-"),
        )
        return source.id


async def test_api_without_token_is_unauthorized():
    async with await _client() as client:
        resp = await client.get(SOURCES_URL)
    assert resp.status_code == 401


async def test_api_with_garbage_token_is_unauthorized():
    async with await _client() as client:
        resp = await client.get(SOURCES_URL, headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


async def test_public_endpoints_stay_reachable_without_a_token():
    async with await _client() as client:
        resp = await client.get("/api/v1/health")
    assert resp.status_code != 401


async def test_oauth_callback_is_public_without_a_token():
    """VK redirects the user's browser here, so it must not require our JWT."""
    async with await _client() as client:
        resp = await client.get("/api/v1/social/callback?code=x&state=y")
    assert resp.status_code != 401


async def test_member_sees_only_its_own_workspace():
    mine, my_token = await _member()
    theirs, _ = await _member()
    mine_id = await _add_source(mine, _uniq("mine-"))
    theirs_id = await _add_source(theirs, _uniq("theirs-"))

    async with await _client() as client:
        resp = await client.get(SOURCES_URL, headers={"Authorization": f"Bearer {my_token}"})

    assert resp.status_code == 200
    returned = {row["id"] for row in resp.json()}
    assert mine_id in returned
    assert theirs_id not in returned


async def test_tenant_header_cannot_widen_access():
    mine, my_token = await _member()
    other, _ = await _member()

    async with await _client() as client:
        resp = await client.get(SOURCES_URL, headers={"Authorization": f"Bearer {my_token}", "X-Tenant-Id": str(other)})

    assert resp.status_code == 403
    assert other != mine


async def test_tenant_header_selects_another_membership():
    first, token = await _member()
    second = await tenants.create(name=_uniq("WS 2 "), slug=_uniq("ws2-"))
    user_id = await _user_id_of_token(token)
    await TenantUserManager().add_web_member(tenant_id=second.id, user_id=user_id, role="owner")
    source_id = await _add_source(second.id, _uniq("second-"))

    async with await _client() as client:
        resp = await client.get(
            SOURCES_URL, headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": str(second.id)}
        )

    assert resp.status_code == 200
    assert source_id in {row["id"] for row in resp.json()}
    assert first != second.id


async def test_user_without_membership_is_forbidden():
    username = _uniq("lonely")
    role = await Role.objects.get(codename=UserRoleType.VIEWER.name)
    user = await User.objects.create_user(
        username=username, email=f"{username}@example.com", password="secret-password-1", role_id=role.id
    )
    token = create_access_token(subject=str(user.id))[0]

    async with await _client() as client:
        resp = await client.get(SOURCES_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 403


async def _user_id_of_token(token: str) -> int:
    from jose import jwt

    from app.core.config import settings

    return int(jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])["sub"])
