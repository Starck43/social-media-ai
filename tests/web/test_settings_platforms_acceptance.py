"""Real ASGI and PostgreSQL acceptance for the settings platforms catalog.

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

from tests.web.test_merged_home_acceptance import existing_target, home
from tests.web.test_web_settings import PASSWORD, _csrf, _login, _name

from app.core.config import settings
from app.core.database import async_engine
from app.core.permissions import operator_permission_scope
from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import Platform, Role, Source, User
from app.types import PlatformRoleType, SourceType

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
        return user

    yield create
    for user in users:
        await User.objects.delete_user(user.id)


@pytest.fixture
async def platform_admin(owned_users):
    return await owned_users(PlatformRoleType.ADMIN.value)


@pytest.fixture
async def owned_platforms():
    rows = []

    def track(row: Platform) -> Platform:
        rows.append(row)
        return row

    yield track
    for row in rows:
        if row is not None:
            await Platform.objects.delete_by_id(row.id)


def _stored_type(row: Platform):
    return getattr(row.platform_type, "db_value", row.platform_type)


def _snapshot(row: Platform):
    return (row.name, row.base_url, _stored_type(row), dict(row.params or {}), bool(row.is_active))


async def _catalog_page(client: AsyncClient, tab: str = "platforms"):
    response = await client.get(f"/app/settings?tab={tab}")
    assert response.status_code == 200
    return response


def _catalog_payload(name: str, **overrides) -> dict:
    data = {
        "name": name,
        "base_url": "https://api.example.invalid/v1",
        "platform_type": "telegram",
        "platform_id": "0",
        "enabled": "on",
    }
    data.update(overrides)
    return data


def _assert_flash_error(response) -> None:
    assert "Укажите название до 50 символов" in response.text or "Не удалось сохранить" in response.text


@pytest.mark.asyncio
async def test_admin_without_workspace_creates_a_platform_through_settings(
    settings_client, platform_admin, owned_platforms
):
    client = settings_client
    name = _name("CatalogPlatform")
    await _login(client, platform_admin.username)
    page = await _catalog_page(client)
    assert 'href="/app/settings?tab=platforms"' in page.text
    assert 'action="/app/settings/platforms"' in page.text
    assert "/admin/platform/list" not in page.text
    assert "/delete" not in page.text

    token = await _csrf(client, "/app/settings?tab=platforms")
    response = await client.post(
        "/app/settings/platforms",
        data={**_catalog_payload(name, platform_type="telegram"), "_csrf": token},
    )
    assert response.status_code == 200
    assert "Платформа сохранена" in response.text
    assert name in response.text

    stored = owned_platforms(await Platform.objects.get_by_name(name, is_active=None))
    assert stored.base_url == "https://api.example.invalid/v1"
    assert _stored_type(stored) == "telegram"
    assert dict(stored.params or {}) == {}
    assert stored.is_active is True


@pytest.mark.asyncio
async def test_admin_update_keeps_type_params_and_source_relationship(
    settings_client, platform_admin, home, owned_platforms
):
    client, platforms = settings_client, owned_platforms
    _, tenant_ids, _ = home
    platform = platforms(
        await Platform.objects.create(
            name=_name("CatalogImmutable"),
            platform_type="vk",
            base_url="https://api.example.invalid/original",
            params={"api_version": "5.199", "api_base_url": "https://api.example.invalid/method"},
            is_active=True,
        )
    )
    source = None
    try:
        with tenant_scope(bypass=True), operator_permission_scope():
            source = await Source.objects.create(
                tenant_id=tenant_ids[0],
                platform_id=platform.id,
                name=_name("CatalogSource"),
                source_type=SourceType.CHANNEL.name,
                external_id=_name("catalog-source"),
                is_active=True,
            )
        await _login(client, platform_admin.username)
        token = await _csrf(client, "/app/settings?tab=platforms")
        renamed = _name("CatalogRenamed")
        response = await client.post(
            "/app/settings/platforms",
            data={
                "name": renamed,
                "base_url": "https://api.example.invalid/updated",
                "platform_type": "telegram",
                "platform_id": str(platform.id),
                "enabled": "",
                "_csrf": token,
            },
        )
        assert response.status_code == 200
        assert "Платформа сохранена" in response.text

        stored = await Platform.objects.get(id=platform.id)
        assert stored.name == renamed
        assert stored.base_url == "https://api.example.invalid/updated"
        assert stored.is_active is False
        assert _stored_type(stored) == "vk"
        assert dict(stored.params or {}) == {
            "api_version": "5.199",
            "api_base_url": "https://api.example.invalid/method",
        }
        with tenant_scope(tenant_ids[0]):
            reloaded_source = await Source.objects.get(id=source.id)
        assert reloaded_source is not None and reloaded_source.platform_id == platform.id
    finally:
        if source is not None:
            with tenant_scope(bypass=True), operator_permission_scope():
                await Source.objects.delete_by_id(source.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("role_code", [PlatformRoleType.OPERATOR.value, PlatformRoleType.USER.value])
async def test_operator_and_user_cannot_read_or_write_the_global_catalog(
    settings_client, owned_users, owned_platforms, role_code
):
    client = settings_client
    actor = await owned_users(role_code)
    platform = owned_platforms(
        await Platform.objects.create(
            name=_name("CatalogGuarded"),
            platform_type="vk",
            base_url="https://api.example.invalid/guarded",
            params={"api_version": "5.199"},
            is_active=True,
        )
    )
    before = _snapshot(await Platform.objects.get(id=platform.id))

    await _login(client, actor.username)
    page = await _catalog_page(client)
    assert 'href="/app/settings?tab=platforms"' not in page.text
    assert 'action="/app/settings/platforms"' not in page.text

    response = await client.post(
        "/app/settings/platforms",
        data={**_catalog_payload("ForbiddenCatalogRename"), "platform_id": str(platform.id), "_csrf": "forged"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert response.headers["location"] == "/app/invite"
    assert _snapshot(await Platform.objects.get(id=platform.id)) == before


@pytest.mark.asyncio
async def test_revoked_or_inactive_admin_cannot_change_storage(settings_client, platform_admin, owned_platforms):
    client = settings_client
    platform = owned_platforms(
        await Platform.objects.create(
            name=_name("CatalogRevoked"),
            platform_type="telegram",
            base_url="https://api.example.invalid/revoked",
            params={"api_version": "keep"},
            is_active=True,
        )
    )
    before = _snapshot(await Platform.objects.get(id=platform.id))
    await _login(client, platform_admin.username)
    token = await _csrf(client, "/app/settings?tab=platforms")
    payload = {**_catalog_payload("RevokedRename"), "platform_id": str(platform.id), "_csrf": token}

    user_role = await Role.objects.get(codename=PlatformRoleType.USER.value)
    assert user_role is not None, "Seeded platform role 'user' is required"
    await User.objects.update_by_id(platform_admin.id, role_id=user_role.id)
    downgraded = await client.post("/app/settings/platforms", data=payload, follow_redirects=False)
    assert downgraded.status_code in (302, 303)
    assert downgraded.headers["location"] == "/app/invite"
    assert _snapshot(await Platform.objects.get(id=platform.id)) == before

    admin_role = await Role.objects.get(codename=PlatformRoleType.ADMIN.value)
    assert admin_role is not None, "Seeded platform role 'admin' is required"
    await User.objects.update_by_id(platform_admin.id, role_id=admin_role.id)
    await User.objects.update_by_id(platform_admin.id, is_active=False)
    deactivated = await client.post("/app/settings/platforms", data=payload, follow_redirects=False)
    assert deactivated.status_code in (302, 303)
    assert "/app/login" in deactivated.headers["location"]
    assert _snapshot(await Platform.objects.get(id=platform.id)) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("supplied", [None, "forged-token"])
async def test_missing_or_invalid_csrf_never_reaches_storage(
    settings_client, platform_admin, owned_platforms, supplied
):
    client = settings_client
    platform = owned_platforms(
        await Platform.objects.create(
            name=_name("CatalogCsrf"),
            platform_type="vk",
            base_url="https://api.example.invalid/csrf",
            params={},
            is_active=True,
        )
    )
    before = _snapshot(await Platform.objects.get(id=platform.id))
    await _login(client, platform_admin.username)
    payload = _catalog_payload("CsrfRename", platform_id=str(platform.id))
    if supplied is not None:
        payload["_csrf"] = supplied
    response = await client.post("/app/settings/platforms", data=payload, follow_redirects=False)
    assert response.status_code == 403
    assert "CSRF" in response.text
    assert _snapshot(await Platform.objects.get(id=platform.id)) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"name": " "},
        {"name": "x" * 51},
        {"base_url": "javascript:alert(1)"},
        {"base_url": "https://user:password@example.invalid"},
        {"base_url": "https://example.invalid:bad"},
        {"platform_id": str(2_147_483_648)},
        {"platform_type": "unknown-type"},
    ],
)
async def test_invalid_or_overflow_values_refuse_cleanly(settings_client, platform_admin, owned_platforms, overrides):
    client = settings_client
    platform = owned_platforms(
        await Platform.objects.create(
            name=_name("CatalogInvalid"),
            platform_type="vk",
            base_url="https://api.example.invalid/invalid",
            params={"api_version": "5.199"},
            is_active=True,
        )
    )
    before = _snapshot(await Platform.objects.get(id=platform.id))
    await _login(client, platform_admin.username)
    token = await _csrf(client, "/app/settings?tab=platforms")
    data = {**_catalog_payload(_name("CatalogAttempt")), **overrides, "_csrf": token}
    if "platform_id" not in overrides:
        # An explicit type only matters for a create; an update ignores it.
        data["platform_id"] = "0" if "platform_type" in overrides else str(platform.id)
    response = await client.post("/app/settings/platforms", data=data)
    assert response.status_code == 200
    _assert_flash_error(response)
    assert _snapshot(await Platform.objects.get(id=platform.id)) == before
    assert await Platform.objects.filter(name=data["name"]).count() == 0


@pytest.mark.asyncio
async def test_missing_row_and_duplicate_name_refuse_without_writes(settings_client, platform_admin, owned_platforms):
    client = settings_client
    existing = owned_platforms(
        await Platform.objects.create(
            name=_name("CatalogDuplicate"),
            platform_type="telegram",
            base_url="https://api.example.invalid/duplicate",
            params={},
            is_active=True,
        )
    )
    await _login(client, platform_admin.username)
    token = await _csrf(client, "/app/settings?tab=platforms")

    missing_name = _name("CatalogMissing")
    missing = await client.post(
        "/app/settings/platforms",
        data={
            **_catalog_payload(missing_name),
            "platform_id": "2147483647",
            "_csrf": token,
        },
    )
    assert missing.status_code == 404
    assert await Platform.objects.filter(name=missing_name).count() == 0

    duplicate = await client.post(
        "/app/settings/platforms",
        data={**_catalog_payload(existing.name), "platform_type": "vk", "_csrf": token},
    )
    assert duplicate.status_code == 200
    assert "Не удалось сохранить" in duplicate.text
    assert await Platform.objects.filter(name=existing.name).count() == 1
