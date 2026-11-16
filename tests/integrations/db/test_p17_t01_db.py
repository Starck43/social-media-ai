"""LOCAL ONLY: P17-T01 PostgreSQL integration; never run on remote lane.

Use root conftest and an exclusively owned, migrated isolated test schema.
All provider calls and secrets are synthetic. See PR deferred scenarios for
schema inventory, migration alignment, cleanup, and the exact tested SHA.
"""

import asyncio
from contextlib import contextmanager

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, func, select

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.integrations.onec import health
from app.integrations.onec.connections import OneCConnectionService
from app.integrations.onec.credentials import open_basic_credentials
from app.integrations.onec.transport import HealthResult
from app.integrations.ratelimit import catalog
from app.models.integrations.connection import WorkspaceConnection, WorkspaceConnectionSecret
from app.models.integrations.rate_budget import ProviderRateBudget
from app.models.integrations.workspace_outbound_host import WorkspaceOutboundHost
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.models.managers.workspace_outbound_host_manager import WorkspaceOutboundHostManager
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401

pytestmark = [pytest.mark.asyncio, pytest.mark.tenancy]
HOST = "erp.example.invalid"


@pytest.fixture
async def world(personal_world, monkeypatch):
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(catalog, "_key", lambda: b"synthetic-test-key-only-32-bytes!")
    tenant_ids = [tenant.id for tenant in personal_world.tenants]
    try:
        yield personal_world
    finally:
        # Both tenants were created by this fixture; never touch another tenant.
        async with async_session_maker() as session, session.begin():
            ids = list(
                (
                    await session.scalars(
                        select(WorkspaceConnection.id).where(WorkspaceConnection.tenant_id.in_(tenant_ids))
                    )
                ).all()
            )
            await session.execute(delete(WorkspaceConnection).where(WorkspaceConnection.tenant_id.in_(tenant_ids)))
            await session.execute(delete(WorkspaceOutboundHost).where(WorkspaceOutboundHost.tenant_id.in_(tenant_ids)))
        async with async_session_maker() as session:
            for model in (WorkspaceConnection, WorkspaceOutboundHost, ProviderRateBudget):
                assert (
                    await session.scalar(select(func.count()).select_from(model).where(model.tenant_id.in_(tenant_ids)))
                    == 0
                )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(WorkspaceConnectionSecret)
                    .where(WorkspaceConnectionSecret.connection_id.in_(ids))
                )
                == 0
            )


@contextmanager
def as_actor(world, tenant, user):
    with tenant_scope(world.tenants[tenant].id), permission_scope(world.users[user]):
        yield {"tenant_id": world.tenants[tenant].id, "actor_id": world.users[user].id}


async def make_connection(service, scope):
    return await service.create(
        **scope,
        label="Synthetic ERP",
        base_url=f"https://{HOST}/odata",
        interface="odata_standard",
        login="synthetic-user",
        password="synthetic-password",
    )


async def test_approval_tenant_ownership_secret_and_reactivation(world):
    hosts, service = WorkspaceOutboundHostManager(), OneCConnectionService()
    with as_actor(world, 0, 0) as owner:
        await hosts.approve(**owner, host=HOST)
        connection = await make_connection(service, owner)
        assert "synthetic-password" not in repr(connection)
        assert "synthetic-user" not in repr(connection)
    async with async_session_maker() as session:
        secret = await session.get(WorkspaceConnectionSecret, connection["id"])
        assert secret and "synthetic-password" not in secret.secret_encrypted
        assert open_basic_credentials(secret.secret_encrypted).password == "synthetic-password"
    with as_actor(world, 0, 1) as other_owner:
        assert await service.list_for_actor(**other_owner) == []
        with pytest.raises(ConnectionDenied):
            await service.disable(**other_owner, connection_id=connection["id"])
    with as_actor(world, 1, 0) as manager:
        with pytest.raises(ConnectionDenied):
            await hosts.approve(**manager, host=HOST)
        with pytest.raises(ConnectionDenied):
            await make_connection(service, manager)
    with as_actor(world, 1, 2) as second_owner:
        await hosts.approve(**second_owner, host=HOST)
    with as_actor(world, 1, 0) as manager:
        second = await make_connection(service, manager)
        assert second["is_active"]
        with pytest.raises(ConnectionDenied):
            await service.disable(**manager, connection_id=connection["id"])
    with as_actor(world, 0, 0) as owner:
        await service.disable(**owner, connection_id=connection["id"])
        updated = await service.replace_credentials(
            **owner, connection_id=connection["id"], login="synthetic-new-user", password="synthetic-new-password"
        )
        assert updated["is_active"] and updated["state"] == "unverified"
        assert "synthetic-new-password" not in repr(updated)
    async with async_session_maker() as session:
        secret = await session.get(WorkspaceConnectionSecret, connection["id"])
        assert open_basic_credentials(secret.secret_encrypted).password == "synthetic-new-password"


async def test_revocation_waits_for_inflight_check_and_cannot_restore_approval(world, monkeypatch):
    hosts, service, checker = WorkspaceOutboundHostManager(), OneCConnectionService(), health.OneCHealthService()
    started, release = asyncio.Event(), asyncio.Event()

    async def fake_check(*args, **kwargs):
        started.set()
        await release.wait()
        return HealthResult("healthy", "a" * 64)

    monkeypatch.setattr(health, "check_health", fake_check)
    with as_actor(world, 0, 0) as owner:
        approval = await hosts.approve(**owner, host=HOST)
        connection = await make_connection(service, owner)
        checking = asyncio.create_task(checker.check(**owner, connection_id=connection["id"]))
        revoking = None
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            revoking = asyncio.create_task(hosts.revoke(**owner, host_id=approval["id"]))
            await asyncio.sleep(0.2)
            assert not revoking.done(), "revocation must wait for the in-flight approved use"
            release.set()
            results = await asyncio.wait_for(asyncio.gather(checking, revoking, return_exceptions=True), timeout=10)
            assert results[1] is None
            assert isinstance(results[0], (dict, ConnectionDenied))
            assert await hosts.list_hosts(**owner) == []
            with pytest.raises(ConnectionDenied):
                await checker.check(**owner, connection_id=connection["id"])
        finally:
            release.set()
            for task in (checking, revoking):
                if task and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in (checking, revoking) if task), return_exceptions=True)


async def test_real_shared_budget_with_mocked_provider(world, monkeypatch):
    hosts, service, checker = WorkspaceOutboundHostManager(), OneCConnectionService(), health.OneCHealthService()

    async def fake_check(endpoint, login, password, *, limiter, budget):
        async with limiter.acquire(budget, cost=2, max_wait=0):
            return HealthResult("healthy", "b" * 64)

    monkeypatch.setattr(health, "check_health", fake_check)
    with as_actor(world, 0, 0) as owner:
        await hosts.approve(**owner, host=HOST)
        connection = await make_connection(service, owner)
        for _ in range(5):
            result = await checker.check(**owner, connection_id=connection["id"])
            assert result["state"] == "verified"
        result = await checker.check(**owner, connection_id=connection["id"])
        assert result["state"] == "error" and result["check_code"] == "rate_limited"
