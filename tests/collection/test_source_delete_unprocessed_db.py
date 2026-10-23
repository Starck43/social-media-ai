"""Admin cleanup of a source's raw staged content through the web boundary."""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import (
    AIAnalytics,
    AnalyzedContentReceipt,
    CollectedItem,
    Job,
    Platform,
    Role,
    Source,
    User,
)
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.types import PlatformRoleType, WorkspaceRoleType

pytestmark = pytest.mark.tenancy

CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')
PASSWORD = "secret-password-1"


async def _client() -> AsyncClient:
    app = create_application()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True)


def _name(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(5)}"


async def _csrf(client: AsyncClient, path: str) -> str:
    response = await client.get(path)
    assert response.status_code == 200
    match = CSRF_RE.search(response.text)
    assert match, f"no CSRF input on {path}"
    return match.group(1)


async def _register(client: AsyncClient, prefix: str) -> tuple[User, int]:
    username = _name(prefix)
    token = await _csrf(client, "/app/register")
    response = await client.post(
        "/app/register",
        data={
            "username": username,
            "email": f"{username}@example.com",
            "password": PASSWORD,
            "workspace": f"{prefix} workspace",
            "_csrf": token,
        },
    )
    assert response.status_code == 200
    user = await User.objects.get(username=username)
    memberships = await TenantUserManager().web_memberships(user.id)
    assert user is not None and memberships
    return user, memberships[0].tenant_id


async def _login(client: AsyncClient, username: str) -> None:
    token = await _csrf(client, "/app/login")
    response = await client.post("/app/login", data={"username": username, "password": PASSWORD, "_csrf": token})
    assert response.status_code == 200


async def _admin(tenant_id: int) -> User:
    role = await Role.objects.get(codename=PlatformRoleType.USER.value)
    username = _name("staged-admin")
    user = await User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=PASSWORD,
        role_id=role.id,
    )
    await TenantUserManager().add_web_member(
        tenant_id=tenant_id,
        user_id=user.id,
        role=WorkspaceRoleType.ADMIN.value,
    )
    return user


async def _source(tenant_id: int, name: str) -> Source:
    from app.types import SourceType

    platform = await Platform.objects.filter(is_active=True).first()
    assert platform is not None, "the test database seeds platforms"
    with tenant_scope(bypass=True):
        return await Source.objects.create(
            tenant_id=tenant_id,
            platform_id=platform.id,
            name=name,
            external_id=_name("source"),
            source_type=SourceType.USER,
            is_active=True,
            params={},
        )


async def _raw_item(tenant_id: int, source_id: int, suffix: str) -> CollectedItem:
    with tenant_scope(bypass=True):
        return await CollectedItem.objects.create(
            tenant_id=tenant_id,
            source_id=source_id,
            external_id=f"raw-{suffix}",
            content_hash=(suffix * 64)[:64],
            content_type="post",
            text=f"raw payload {suffix}",
            attachments=[],
            metrics={},
            author={},
        )


async def _drop(users: list[User], tenant_ids: list[int], job_ids: list[int] | None = None) -> None:
    if job_ids:
        with tenant_scope(bypass=True):
            for job_id in job_ids:
                await Job.objects.delete_by_id(job_id)
    for user in users:
        await User.objects.delete_user(user.id)
    for tenant_id in tenant_ids:
        await tenants.delete_by_id(tenant_id)


async def test_source_detail_offers_raw_cleanup_only_to_workspace_admin():
    owner, tenant_id = None, None
    admin = source = None
    async with await _client() as owner_client:
        owner, tenant_id = await _register(owner_client, "raw-cleanup-page")
        try:
            source = await _source(tenant_id, "cleanup page source")
            await _login(owner_client, owner.username)
            owner_page = await owner_client.get(f"/app/sources/{source.id}")
            assert owner_page.status_code == 200
            assert f'action="/app/sources/{source.id}/delete-unprocessed"' not in owner_page.text

            admin = await _admin(tenant_id)
            async with await _client() as admin_client:
                await _login(admin_client, admin.username)
                admin_page = await admin_client.get(f"/app/sources/{source.id}")
                assert admin_page.status_code == 200
                form = re.search(
                    rf'<form[^>]*action="/app/sources/{source.id}/delete-unprocessed".*?</form>',
                    admin_page.text,
                    re.DOTALL,
                )
                assert form, "workspace admin should see the raw staged cleanup action"
                assert "_csrf" in form.group(0)
        finally:
            await _drop([user for user in (owner, admin) if user], [tenant_id] if tenant_id else [])


async def test_cleanup_requires_valid_csrf_and_admin_membership():
    owner, tenant_id = None, None
    source = None
    async with await _client() as client:
        owner, tenant_id = await _register(client, "raw-cleanup-auth")
        try:
            source = await _source(tenant_id, "auth source")
            await _login(client, owner.username)
            with tenant_scope(bypass=True):
                item = await _raw_item(tenant_id, source.id, "a")

            bad_csrf = await client.post(
                f"/app/sources/{source.id}/delete-unprocessed", data={"_csrf": "invalid"}
            )
            assert bad_csrf.status_code == 200
            assert "Сессия истекла" in bad_csrf.text

            valid_csrf = await _csrf(client, f"/app/sources/{source.id}")
            non_admin = await client.post(
                f"/app/sources/{source.id}/delete-unprocessed", data={"_csrf": valid_csrf}
            )
            assert non_admin.status_code == 200
            assert "только администратор" in non_admin.text
            with tenant_scope(bypass=True):
                assert await CollectedItem.objects.get(id=item.id) is not None
        finally:
            await _drop([owner] if owner else [], [tenant_id] if tenant_id else [])


async def test_foreign_workspace_admin_cannot_clean_another_tenants_source():
    owner_a = owner_b = admin_b = None
    tenant_a = tenant_b = None
    source_a = None
    async with await _client() as client_a:
        owner_a, tenant_a = await _register(client_a, "raw-cleanup-a")
    async with await _client() as client_b:
        owner_b, tenant_b = await _register(client_b, "raw-cleanup-b")
    try:
        source_a = await _source(tenant_a, "foreign source")
        admin_b = await _admin(tenant_b)
        with tenant_scope(bypass=True):
            item = await _raw_item(tenant_a, source_a.id, "b")
        async with await _client() as client:
            await _login(client, admin_b.username)
            token = await _csrf(client, "/app/sources")
            response = await client.post(
                f"/app/sources/{source_a.id}/delete-unprocessed",
                data={"_csrf": token, "tenant_id": str(tenant_a)},
            )
            assert response.status_code == 200
            assert "Источник не найден" in response.text
        with tenant_scope(bypass=True):
            assert await CollectedItem.objects.get(id=item.id) is not None
            assert await Source.objects.get(id=source_a.id) is not None
    finally:
        await _drop(
            [user for user in (owner_a, owner_b, admin_b) if user],
            [tenant for tenant in (tenant_a, tenant_b) if tenant],
        )


@pytest.mark.parametrize(("job_type", "status"), [("collect", "pending"), ("analyze", "running")])
async def test_cleanup_refuses_queued_or_running_source_job(job_type: str, status: str):
    owner, admin, tenant_id = None, None, None
    source = None
    job_id = None
    async with await _client() as client:
        owner, tenant_id = await _register(client, "raw-cleanup-busy")
        try:
            admin = await _admin(tenant_id)
            source = await _source(tenant_id, "busy source")
            with tenant_scope(bypass=True):
                item = await _raw_item(tenant_id, source.id, "c")
                job = await Job.objects.create(
                    tenant_id=tenant_id,
                    job_type=job_type,
                    status=status,
                    payload={"source_ids": [source.id]},
                    run_at=datetime.now(timezone.utc),
                )
            job_id = job.id
            await _login(client, admin.username)
            token = await _csrf(client, f"/app/sources/{source.id}")
            response = await client.post(
                f"/app/sources/{source.id}/delete-unprocessed", data={"_csrf": token}
            )
            assert response.status_code == 200
            busy_message = "пока сбор или анализ ожидает запуска или выполняется"
            assert busy_message in response.text
            with tenant_scope(bypass=True):
                assert await CollectedItem.objects.get(id=item.id) is not None
        finally:
            await _drop(
                [user for user in (owner, admin) if user],
                [tenant_id] if tenant_id else [],
                [job_id] if job_id else [],
            )


async def test_cleanup_deletes_raw_rows_but_preserves_analytics_and_analysis_receipts():
    owner = admin = None
    tenant_id = None
    source = None
    async with await _client() as client:
        owner, tenant_id = await _register(client, "raw-cleanup-data")
        try:
            admin = await _admin(tenant_id)
            source = await _source(tenant_id, "data source")
            with tenant_scope(bypass=True):
                raw_items = [await _raw_item(tenant_id, source.id, value) for value in ("d", "e")]
                analytics = await AIAnalytics.objects.create(
                    tenant_id=tenant_id,
                    source_id=source.id,
                    content_hash="a" * 64,
                    analysis_fingerprint="cleanup-test",
                    summary_data={"summary": "keep"},
                )
                receipt = await AnalyzedContentReceipt.objects.create(
                    tenant_id=tenant_id,
                    source_id=source.id,
                    content_hash="a" * 64,
                    analysis_fingerprint="cleanup-test",
                    analytics_id=analytics.id,
                )
            await _login(client, admin.username)
            token = await _csrf(client, f"/app/sources/{source.id}")
            response = await client.post(
                f"/app/sources/{source.id}/delete-unprocessed", data={"_csrf": token}
            )
            assert response.status_code == 200
            assert "Удалено необработанных записей: 2" in response.text
            with tenant_scope(bypass=True):
                assert await CollectedItem.objects.filter(source_id=source.id).count() == 0
                assert await AIAnalytics.objects.get(id=analytics.id) is not None
                assert await AnalyzedContentReceipt.objects.get(id=receipt.id) is not None
                assert await Source.objects.get(id=source.id) is not None
        finally:
            await _drop([user for user in (owner, admin) if user], [tenant_id] if tenant_id else [])
