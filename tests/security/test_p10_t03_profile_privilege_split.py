"""P10-T03 (AUD-03): a profile update is not a privilege update.

`docs/audits/2026-10-15-dev.md#aud-03` reported that `UserUpdate` accepted
`is_superuser`/`is_active` and the endpoint forwarded them to storage, so a
holder of `user.update` could raise its own platform authority. `eb6cad6`
replaced that design: the profile input is `username`/`email` only, the manager
refuses any other key and re-reads the acting account, and platform roles moved
to their own route. These tests pin the replacement through the real endpoint
and the real manager, at both the self-service and the operator level, so a
future widening of the profile input cannot silently restore the escalation.
"""

from __future__ import annotations

import re
import secrets

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import PlatformRole, User
from app.models.managers.tenant_manager import tenants
from app.schemas.user import UserUpdate
from app.types import PlatformRoleType

PASSWORD = "secret-password-1"

#: Everything the audited `UserUpdate` used to accept, plus the neighbouring
#: authority columns. None of it may reach storage through a profile route.
PRIVILEGE_FIELDS: dict[str, object] = {
    "is_superuser": True,
    "is_active": False,
    "is_platform_admin": True,
    "role_id": 1,
    "role": PlatformRoleType.ADMIN.value,
    "hashed_password": "synthetic-hash-never-a-credential",
    "id": 1,
}


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
    """Sign up through the web UI: the account owns a fresh workspace."""
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
    from app.models.managers.tenant_manager import TenantUserManager

    memberships = await TenantUserManager().web_memberships(user.id)
    assert user is not None and memberships
    return user, memberships[0].tenant_id


async def _register_api(client: AsyncClient, prefix: str) -> tuple[User, dict[str, str]]:
    """Sign up through the machine API: the account gets no workspace."""
    username = f"{prefix}{secrets.token_hex(3)}"
    resp = await client.post(
        "/api/v1/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    user = await User.objects.get(username=username)
    return user, {"Authorization": f"Bearer {resp.json()['access_token']}"}


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


async def _promote(user: User, role: PlatformRoleType) -> None:
    """Test setup only: write the role column the operator routes would assign."""
    platform_role = await PlatformRole.objects.get_by_codename(role)
    assert platform_role is not None
    await User.objects.update_by_id(user.id, role_id=platform_role.id)


# ── the input contract itself ───────────────────────────────────────────────


def test_profile_input_carries_only_self_service_fields() -> None:
    assert set(UserUpdate.model_fields) == {"username", "email"}
    assert UserUpdate(**PRIVILEGE_FIELDS).model_dump(exclude_unset=True) == {}


# ── self-service profile update ─────────────────────────────────────────────


async def test_self_service_profile_update_cannot_raise_authority() -> None:
    async with await _client() as c:
        actor, tenant_id = await _register(c, "PrivSelf")
    try:
        before = await User.objects.prefetch_related("role").get(id=actor.id)
        assert before.role.codename == PlatformRoleType.USER.value
        async with await _client() as c:
            headers = await _bearer(c, actor.username)
            renamed = f"{actor.username}new"
            resp = await c.put("/api/v1/users/me", json={"username": renamed, **PRIVILEGE_FIELDS}, headers=headers)
            assert resp.status_code == 200, resp.text
        after = await User.objects.prefetch_related("role").get(id=actor.id)
        # The allowed field changed; every authority column and the identity did not.
        assert after.username == renamed
        assert after.id == before.id
        assert after.role_id == before.role_id
        assert after.role.codename == PlatformRoleType.USER.value
        assert after.is_active is True
        assert after.is_superuser is False
        assert after.hashed_password == before.hashed_password
    finally:
        await _drop([actor], [tenant_id])


async def test_manager_refuses_a_privilege_field_without_the_schema() -> None:
    async with await _client() as c:
        actor, tenant_id = await _register(c, "PrivMgr")
    try:
        before = await User.objects.get(id=actor.id)
        # The endpoint would strip this key first, so the manager is asked
        # directly: the refusal must not depend on the schema alone.
        with pytest.raises(HTTPException) as refusal:
            await User.objects.update_user(
                user_id=actor.id,
                update_data={"is_superuser": True},
                current_user=actor,
            )
        assert refusal.value.status_code == 400
        after = await User.objects.get(id=actor.id)
        assert after.role_id == before.role_id
        assert after.is_active is True
    finally:
        await _drop([actor], [tenant_id])


async def test_self_service_cannot_update_another_account() -> None:
    async with await _client() as c:
        actor, actor_tenant = await _register(c, "PrivCross")
        other, other_tenant = await _register(c, "PrivOther")
    try:
        before = await User.objects.get(id=other.id)
        async with await _client() as c:
            headers = await _bearer(c, actor.username)
            resp = await c.put(f"/api/v1/users/{other.id}", json={"username": "stolen"}, headers=headers)
            assert resp.status_code == 403, resp.text
        after = await User.objects.get(id=other.id)
        assert after.username == before.username
    finally:
        await _drop([actor, other], [actor_tenant, other_tenant])


async def test_api_only_account_keeps_self_service_but_no_workspace_data() -> None:
    """`docs/API.md`: API registration creates no membership; the profile route
    is account-level, so the account can manage itself but cannot read workspace
    data until a membership exists."""
    async with await _client() as c:
        actor, headers = await _register_api(c, "PrivApiOnly")
        try:
            assert (await c.get("/api/v1/users/me", headers=headers)).status_code == 200
            resp = await c.put("/api/v1/users/me", json={"username": f"{actor.username}new"}, headers=headers)
            assert resp.status_code == 200, resp.text
            workspace = await c.get("/api/v1/sources", headers=headers)
            assert workspace.status_code == 403
            assert workspace.json()["detail"] == "User has no workspace membership"
        finally:
            await _drop([actor], [])


# ── privileged profile update ───────────────────────────────────────────────


async def test_operator_profile_update_keeps_the_target_authority() -> None:
    async with await _client() as c:
        operator, operator_tenant = await _register(c, "PrivOper")
        target, target_tenant = await _register(c, "PrivTarget")
    try:
        await _promote(operator, PlatformRoleType.ADMIN)
        before = await User.objects.prefetch_related("role").get(id=target.id)
        async with await _client() as c:
            headers = await _bearer(c, operator.username)
            renamed = f"{target.username}new"
            resp = await c.put(
                f"/api/v1/users/{target.id}",
                json={"username": renamed, **PRIVILEGE_FIELDS},
                headers=headers,
            )
            assert resp.status_code == 200, resp.text
        after = await User.objects.prefetch_related("role").get(id=target.id)
        assert after.username == renamed
        assert after.role_id == before.role_id
        assert after.role.codename == PlatformRoleType.USER.value
        assert after.is_active is True
        assert after.hashed_password == before.hashed_password
    finally:
        await _drop([operator, target], [operator_tenant, target_tenant])


async def test_revoked_operator_grant_fails_closed_on_the_next_request() -> None:
    async with await _client() as c:
        operator, operator_tenant = await _register(c, "PrivRevoke")
        target, target_tenant = await _register(c, "PrivRevTarget")
    try:
        await _promote(operator, PlatformRoleType.ADMIN)
        async with await _client() as c:
            headers = await _bearer(c, operator.username)
            assert (
                await c.put(f"/api/v1/users/{target.id}", json={"username": "first"}, headers=headers)
            ).status_code == 200

            # Revoke between the two requests: the second one must not reuse the
            # identity the first one was authorized with.
            await _promote(operator, PlatformRoleType.USER)
            denied = await c.put(f"/api/v1/users/{target.id}", json={"username": "second"}, headers=headers)
            assert denied.status_code == 403, denied.text

        after = await User.objects.get(id=target.id)
        assert after.username == "first"
    finally:
        await _drop([operator, target], [operator_tenant, target_tenant])


async def test_platform_role_changes_only_through_the_dedicated_route() -> None:
    async with await _client() as c:
        operator, operator_tenant = await _register(c, "PrivRole")
        target, target_tenant = await _register(c, "PrivRoleTarget")
    try:
        await _promote(operator, PlatformRoleType.ADMIN)
        async with await _client() as c:
            operator_headers = await _bearer(c, operator.username)
            target_headers = await _bearer(c, target.username)

            # A profile route never carries a role, whatever the caller holds.
            denied = await c.put(
                f"/api/v1/users/{target.id}/platform-role",
                json={"role": PlatformRoleType.OPERATOR.value},
                headers=target_headers,
            )
            assert denied.status_code == 403, denied.text

            promoted = await c.put(
                f"/api/v1/users/{target.id}/platform-role",
                json={"role": PlatformRoleType.OPERATOR.value},
                headers=operator_headers,
            )
            assert promoted.status_code == 200, promoted.text
            role = await PlatformRole.objects.get_by_codename(PlatformRoleType.OPERATOR)
            assert (await User.objects.get(id=target.id)).role_id == role.id

            # The owner role is minted by migration only, never by an operator.
            refused = await c.put(
                f"/api/v1/users/{target.id}/platform-role",
                json={"role": PlatformRoleType.SUPERUSER.value},
                headers=operator_headers,
            )
            assert refused.status_code == 403, refused.text
            assert (await User.objects.get(id=target.id)).role_id == role.id
    finally:
        await _drop([operator, target], [operator_tenant, target_tenant])
