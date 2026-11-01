"""Real ASGI and PostgreSQL regression for the removed settings platforms catalog.

The approved target guard and the shared application fixtures come from the
neighbouring acceptance module, so resolution happens before application
imports. Every row here is synthetic, owned by this module and removed again;
the seeded platform roles and catalog are read, never reset.
"""

from __future__ import annotations

import sys
from pathlib import Path

# isort: off
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from tests.web.test_merged_home_acceptance import existing_target
from tests.web.test_web_settings import PASSWORD, _csrf, _login, _name

from app.core.config import settings
from app.core.database import async_engine
from app.core.permissions import operator_permission_scope
from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import Platform, Role, Source, Tenant, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.types import PlatformRoleType

# isort: on

pytestmark = pytest.mark.tenancy


@pytest.fixture(scope="module", autouse=True)
async def catalog_tables_ready(existing_target):
    async with async_engine.connect() as connection:
        async with connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            for model in (Platform, Source):
                present = await connection.scalar(
                    text("SELECT to_regclass(:table)"),
                    {"table": f"{settings.DB_SCHEMA}.{model.__table__.name}"},
                )
                assert present is not None, f"Existing table required for catalog acceptance: {model.__table__.name}"


@pytest.fixture
async def settings_client():
    app = create_application()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=True) as client:
        yield client


@pytest.fixture
async def owned_users():
    users = []
    workspaces = []

    async def create(role_code: str) -> User:
        role = await Role.objects.get(codename=role_code)
        assert role is not None, f"Seeded platform role {role_code!r} is required; never seed here"
        username = _name("CatalogActor")
        user = await User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password=PASSWORD,
            role_id=role.id,
        )
        users.append(user)
        with tenant_scope(bypass=True), operator_permission_scope():
            workspace = await Tenant.objects.create(name=username, slug=username.lower())
            workspaces.append(workspace)
            await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=user.id, role="owner")
        return user

    yield create
    with tenant_scope(bypass=True), operator_permission_scope():
        for workspace in workspaces:
            await tenants.delete_by_id(workspace.id)
        for user in users:
            await User.objects.delete_by_id(user.id)


@pytest.fixture
async def platform_admin(owned_users):
    return await owned_users(PlatformRoleType.ADMIN.value)


@pytest.mark.parametrize("role", ["user", "operator", "admin", "superuser"])
async def test_removed_catalog_routes_never_change_platforms(settings_client, owned_users, role):
    actor = await owned_users(role)
    await _login(settings_client, actor.username)
    before = [
        (p.id, p.name, dict(p.params), p.is_active) for p in await Platform.objects.filter().order_by(Platform.id)
    ]
    page = await settings_client.get("/app/settings?tab=platforms")
    assert page.status_code == 200
    assert "tab=platforms" not in page.text
    assert "/app/settings/platforms" not in page.text
    response = await settings_client.post(
        "/app/settings/platforms",
        follow_redirects=False,
        data={
            "name": "MustNotExist",
            "platform_type": "vk",
            "base_url": "https://example.invalid",
            "params": '{"api_version":"changed"}',
            "_csrf": await _csrf(settings_client, "/app/settings"),
        },
    )
    assert response.status_code in (404, 405)
    after = [(p.id, p.name, dict(p.params), p.is_active) for p in await Platform.objects.filter().order_by(Platform.id)]
    assert after == before
