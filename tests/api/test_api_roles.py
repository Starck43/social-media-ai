"""`/api/v1/users/roles` — role lookup, permissions and the ADMIN gate.

Regressions covered here are the ones a linter flags but only a request finds:
an un-awaited manager call, a `db=` argument the method never accepted, a
rollback that was a coroutine nobody awaited, and `select_related` on a m2m,
which does not fail — it quietly returns roles with no permissions at all.
"""

import secrets

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import Permission, PlatformRole
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.types import PlatformRoleType, WorkspaceRoleType
from app.utils.token import create_access_token

pytestmark = pytest.mark.tenancy

ROLES_URL = "/api/v1/users/roles"  # the collection route is registered with a trailing slash


def _uniq(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4)}"


async def _user_with_role(codename: str) -> str:
    """Register a user with the given platform role and a workspace; return a token."""
    username = _uniq("roles")
    role = await PlatformRole.objects.get(codename=PlatformRoleType[codename].value)
    from app.models import User

    user = await User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="secret-password-1",
        role_id=role.id,
    )
    tenant = await tenants.create(name=_uniq("WS "), slug=_uniq("ws-"))
    await TenantUserManager().add_web_member(tenant_id=tenant.id, user_id=user.id, role="owner")
    return create_access_token(subject=str(user.id))[0]


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=create_application()), base_url="http://testserver")


async def test_roles_list_requires_authentication():
    async with await _client() as client:
        resp = await client.get(f"{ROLES_URL}/")
    assert resp.status_code == 401


async def test_roles_list_includes_permissions():
    """A m2m needs prefetch_related: select_related returns empty collections.

    Reading the authorisation catalogue is an administrator's right: the
    operator role does not hold `identity.platform_role.view`.
    """
    token = await _user_with_role("ADMIN")
    async with await _client() as client:
        resp = await client.get(f"{ROLES_URL}/", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text
    roles = resp.json()["items"]
    assert roles, "no roles returned"
    # The administrator role carries the platform grants; the operator's own
    # grants are empty until an administrator configures them.
    administrator = next(r for r in roles if r["codename"] == "admin")
    assert len(administrator["permissions"]) > 0


async def test_get_role_returns_its_permissions():
    token = await _user_with_role("ADMIN")
    async with await _client() as client:
        resp = await client.get(f"{ROLES_URL}/admin", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["codename"] == "admin"
    assert resp.json()["label"] == "Администратор платформы"
    assert len(resp.json()["permissions"]) > 0


async def test_get_unknown_role_is_404():
    token = await _user_with_role("ADMIN")
    async with await _client() as client:
        resp = await client.get(f"{ROLES_URL}/nope", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 404


async def test_permission_update_requires_admin():
    token = await _user_with_role("OPERATOR")
    payload = {"permissions": ["social.Source.view"], "strategy": "replace"}

    async with await _client() as client:
        resp = await client.put(
            f"{ROLES_URL}/admin/permissions", json=payload, headers={"Authorization": f"Bearer {token}"}
        )

    assert resp.status_code == 403


async def test_admin_can_update_role_permissions():
    """The ADMIN gate passes and the service really writes the new set."""
    from app.services.user.permissions import RolePermissionService

    with tenant_scope(bypass=True):
        perms = list(await Permission.objects.order_by(Permission.id).all())
    assert len(perms) >= 2, "the database has too few permissions for this test"
    first, second = perms[0].codename, perms[1].codename

    role = await PlatformRole.objects.get_by_codename(PlatformRoleType.OPERATOR)
    original = await RolePermissionService.get_role_permissions(role.id)
    # The arrangement writes the role's stored grants directly; the write path
    # only accepts a platform administrator, so the platform authority is used
    # here — the request below is what proves the *API* path.
    with tenant_scope(bypass=True):
        await RolePermissionService._set_role_permissions(role.id, [perms[0].id, perms[1].id])

    token = await _user_with_role("ADMIN")
    try:
        async with await _client() as client:
            resp = await client.put(
                f"{ROLES_URL}/operator/permissions",
                json={"permissions": [first], "strategy": "replace"},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["changes"]["removed"] == [second]
        assert body["role"]["codename"] == "operator"
        assert body["role"]["label"] == "Оператор"
        assert [p["codename"] for p in body["role"]["permissions"]] == [first]

        stored = await RolePermissionService.get_role_permissions(role.id)
        assert [p.codename for p in stored] == [first]
    finally:
        with tenant_scope(bypass=True):
            await RolePermissionService._set_role_permissions(role.id, [p.id for p in original])


async def test_permission_update_reports_unknown_role():
    """Only the operator role's grants may be changed; a fixed role is refused."""
    token = await _user_with_role("ADMIN")
    payload = {"permissions": ["social.notification.view"], "strategy": "replace"}

    async with await _client() as client:
        resp = await client.put(
            f"{ROLES_URL}/definitely-not-a-role/permissions", json=payload, headers={"Authorization": f"Bearer {token}"}
        )

    assert resp.status_code == 400, resp.text
    assert "operator grants" in resp.text.lower()
