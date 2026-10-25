"""Real ASGI acceptance for platform-role entry to SQLAdmin."""

from __future__ import annotations

import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# isort: off
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest
from httpx import ASGITransport, AsyncClient

from tests.web.test_merged_home_acceptance import actor, existing_target, home

from app.core.permissions import operator_permission_scope
from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import Role, User

# isort: on

pytestmark = pytest.mark.tenancy
PASSWORD = "secret-password-1"
CSRF = re.compile(r'name="csrf_token"[^>]*value="([^"]+)"')


@asynccontextmanager
async def _admin_client():
    async with AsyncClient(
        transport=ASGITransport(app=create_application()),
        base_url="http://testserver",
        follow_redirects=True,
    ) as client:
        yield client


async def _login(client: AsyncClient, username: str) -> None:
    page = await client.get("/admin/login")
    assert page.status_code == 200
    match = CSRF.search(page.text)
    assert match, "SQLAdmin login page must render its CSRF token"
    await client.post(
        "/admin/login",
        data={"username": username, "password": PASSWORD, "csrf_token": match.group(1)},
    )


async def _set_platform_identity(user: User, role_name: str, *, active: bool | None = None) -> None:
    role = await Role.objects.get(codename=role_name)
    assert role is not None, f"Seeded platform role {role_name!r} is required"
    changes = {"role_id": role.id}
    if active is not None:
        changes["is_active"] = active
    with tenant_scope(bypass=True), operator_permission_scope():
        await User.objects.update_by_id(user.id, **changes)


async def _denied_to_login(client: AsyncClient, path: str) -> None:
    response = await client.get(path, follow_redirects=False)
    assert response.status_code in (302, 303)
    assert "/admin/login" in response.headers.get("location", "")


@pytest.mark.asyncio
async def test_workspace_owner_with_platform_user_role_cannot_enter_admin(home, existing_target):
    owner = home[0][0]
    async with _admin_client() as client:
        await _login(client, owner.username)
        await _denied_to_login(client, "/admin/source/list")


@pytest.mark.asyncio
async def test_operator_and_admin_enter_while_resource_grants_stay_independent(home, existing_target):
    operator = await actor(home, "viewer")
    await _set_platform_identity(operator, "operator")
    administrator = await actor(home, "admin")

    with tenant_scope(bypass=True):
        loaded_operator = await User.objects.prefetch_related("role.permissions.model_type").get(id=operator.id)
        source_access = bool(loaded_operator.model_permissions("source"))

    async with _admin_client() as operator_client:
        await _login(operator_client, operator.username)
        index = await operator_client.get("/admin/")
        assert index.status_code == 200
        assert ("/admin/source/list" in index.text) is source_access
        source = await operator_client.get("/admin/source/list", follow_redirects=False)
        assert source.status_code == (200 if source_access else 403)

    async with _admin_client() as admin_client:
        await _login(admin_client, administrator.username)
        index = await admin_client.get("/admin/")
        assert index.status_code == 200
        assert "/admin/user/list" in index.text
        assert (await admin_client.get("/admin/user/list")).status_code == 200


@pytest.mark.asyncio
async def test_downgraded_logged_in_admin_is_denied_on_next_request(home, existing_target):
    administrator = await actor(home, "admin")
    async with _admin_client() as client:
        await _login(client, administrator.username)
        assert (await client.get("/admin/")).status_code == 200
        await _set_platform_identity(administrator, "user")
        await _denied_to_login(client, "/admin/user/list")


@pytest.mark.asyncio
async def test_disabled_logged_in_admin_is_denied_on_next_request(home, existing_target):
    administrator = await actor(home, "admin")
    async with _admin_client() as client:
        await _login(client, administrator.username)
        assert (await client.get("/admin/")).status_code == 200
        await _set_platform_identity(administrator, "admin", active=False)
        await _denied_to_login(client, "/admin/user/list")
