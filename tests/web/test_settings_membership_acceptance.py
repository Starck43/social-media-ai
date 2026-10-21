"""Real ASGI and PostgreSQL acceptance for workspace team mutations."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# isort: off
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select, text, update

from tests.web.test_merged_home_acceptance import actor, existing_target, home
from tests.web.test_web_settings import _csrf, _login

from app.core.config import settings
from app.core.database import async_engine, async_session_maker
from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import Tenant, TenantUser, WorkspaceRole
from app.models.managers.tenant_manager import TenantUserManager

# isort: on

pytestmark = pytest.mark.tenancy


@pytest.fixture(scope="module", autouse=True)
async def membership_tables_ready(existing_target):
    async with async_engine.connect() as connection:
        async with connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            for table in (Tenant, TenantUser, WorkspaceRole):
                present = await connection.scalar(
                    text("SELECT to_regclass(:table)"),
                    {"table": f"{settings.DB_SCHEMA}.{table.__table__.name}"},
                )
                assert present is not None, f"Existing table required for team acceptance: {table.__table__.name}"


@pytest.fixture
async def settings_client():
    app = create_application()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=True) as client:
        yield client


async def _membership(tenant_id: int, user_id: int) -> TenantUser:
    with tenant_scope(tenant_id):
        rows = await TenantUserManager().web_memberships_for_tenant(tenant_id)
    return next(row for row in rows if row.user_id == user_id)


async def _assign_role(tenant_id: int, user_id: int, codename: str) -> TenantUser:
    role = await WorkspaceRole.objects.get(codename=codename)
    assert role is not None, f"Existing workspace role {codename!r} is required"
    membership = await _membership(tenant_id, user_id)
    with tenant_scope(tenant_id):
        await TenantUserManager().update_by_id(membership.id, role_id=role.id)
        return await TenantUserManager().get(id=membership.id)


async def _stored_membership(tenant_id: int, membership_id: int) -> TenantUser:
    with tenant_scope(tenant_id):
        return await TenantUserManager().get(id=membership_id)


@pytest.mark.asyncio
async def test_owner_can_disable_member_through_settings_endpoint(settings_client, home):
    client = settings_client
    users, tenant_ids, _ = home
    owner = users[0]
    member = await actor(home, "manager")
    membership = await _membership(tenant_ids[0], member.id)

    await _login(client, owner.username)
    token = await _csrf(client, "/app/settings?tab=team")
    response = await client.post(
        f"/app/settings/members/{membership.id}/disable",
        data={"_csrf": token},
    )

    assert response.status_code == 200
    assert "Доступ участника отключён" in response.text
    stored = await _stored_membership(tenant_ids[0], membership.id)
    assert stored is not None and stored.is_active is False


@pytest.mark.asyncio
async def test_self_role_and_workspace_admin_restrictions(settings_client, home):
    client = settings_client
    users, tenant_ids, _ = home
    owner = users[0]
    first_admin = await actor(home, "viewer")
    second_admin = await actor(home, "viewer")
    tenant_id = tenant_ids[0]
    owner_membership = await _membership(tenant_id, owner.id)
    admin_role = await WorkspaceRole.objects.get(codename="admin")
    owner_role = await WorkspaceRole.objects.get(codename="owner")
    await _assign_role(tenant_id, first_admin.id, "admin")
    second_row = await _assign_role(tenant_id, second_admin.id, "admin")

    await _login(client, owner.username)
    token = await _csrf(client, "/app/settings?tab=team")
    response = await client.post(
        f"/app/settings/members/{owner_membership.id}/role",
        data={"role": "viewer", "_csrf": token},
    )
    assert response.status_code == 200
    assert "Нельзя менять собственную роль" in response.text
    assert (await _stored_membership(tenant_id, owner_membership.id)).role_id == owner_role.id

    await _login(client, first_admin.username)
    token = await _csrf(client, "/app/settings?tab=team")
    response = await client.post(
        f"/app/settings/members/{second_row.id}/role",
        data={"role": "manager", "_csrf": token},
    )
    assert response.status_code == 200
    assert "Недостаточно прав" in response.text
    assert (await _stored_membership(tenant_id, second_row.id)).role_id == admin_role.id

    response = await client.post(
        f"/app/settings/members/{owner_membership.id}/disable",
        data={"_csrf": token},
    )
    assert response.status_code == 200
    assert (await _stored_membership(tenant_id, owner_membership.id)).is_active is True
    assert second_row.role_id == admin_role.id


@pytest.mark.asyncio
async def test_revoked_admin_cannot_finish_inflight_team_mutation(settings_client, home):
    client = settings_client
    users, tenant_ids, _ = home
    admin = await actor(home, "viewer")
    target = await actor(home, "viewer")
    tenant_id = tenant_ids[0]
    admin_membership = await _assign_role(tenant_id, admin.id, "admin")
    target_membership = await _membership(tenant_id, target.id)
    await _login(client, admin.username)
    token = await _csrf(client, "/app/settings?tab=team")

    lock_waiting = asyncio.Event()
    loop = asyncio.get_running_loop()

    def observe_workspace_lock(connection, cursor, statement, parameters, context, executemany):
        normalized = statement.replace('"', "").lower()
        if "for update" in normalized and f"{settings.DB_SCHEMA.lower()}.tenants" in normalized:
            loop.call_soon_threadsafe(lock_waiting.set)

    async with async_session_maker() as held_session:
        await held_session.begin()
        await held_session.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())
        event.listen(async_engine.sync_engine, "before_cursor_execute", observe_workspace_lock)
        request_task = None
        try:
            request_task = asyncio.create_task(
                client.post(
                    f"/app/settings/members/{target_membership.id}/disable",
                    data={"_csrf": token},
                )
            )
            await asyncio.wait_for(lock_waiting.wait(), timeout=10)
            with tenant_scope(tenant_id):
                await held_session.execute(
                    update(TenantUser).where(TenantUser.id == admin_membership.id).values(is_active=False)
                )
            await held_session.commit()
            response = await asyncio.wait_for(request_task, timeout=10)
        finally:
            event.remove(async_engine.sync_engine, "before_cursor_execute", observe_workspace_lock)
            if request_task is not None and not request_task.done():
                request_task.cancel()
                await asyncio.gather(request_task, return_exceptions=True)
            if held_session.in_transaction():
                await held_session.rollback()

    assert response.status_code == 200
    assert "Участник не найден или защищён как владелец" in response.text
    assert (await _stored_membership(tenant_id, admin_membership.id)).is_active is False
    assert (await _stored_membership(tenant_id, target_membership.id)).is_active is True


@pytest.mark.asyncio
async def test_database_failure_rolls_back_team_role_change(settings_client, home):
    client = settings_client
    users, tenant_ids, _ = home
    owner = users[0]
    target = await actor(home, "viewer")
    membership = await _membership(tenant_ids[0], target.id)
    await _login(client, owner.username)
    token = await _csrf(client, "/app/settings?tab=team")
    injected = False

    def fail_membership_update(connection, cursor, statement, parameters, context, executemany):
        nonlocal injected
        normalized = statement.replace('"', "").lower()
        if not injected and f"update {settings.DB_SCHEMA.lower()}.tenant_users" in normalized:
            injected = True
            raise RuntimeError("injected acceptance failure")

    event.listen(async_engine.sync_engine, "before_cursor_execute", fail_membership_update)
    try:
        response = await client.post(
            f"/app/settings/members/{membership.id}/role",
            data={"role": "manager", "_csrf": token},
        )
    finally:
        event.remove(async_engine.sync_engine, "before_cursor_execute", fail_membership_update)

    assert injected and response.status_code == 500
    stored = await _stored_membership(tenant_ids[0], membership.id)
    assert stored.role.codename == "viewer"
