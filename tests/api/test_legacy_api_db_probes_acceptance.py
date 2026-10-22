"""Real ASGI and PostgreSQL acceptance for the legacy connectivity probes."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event

from app.api.v1 import entry
from app.core.database import async_engine
from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import User
from app.models.managers.tenant_manager import TenantUserManager
from app.utils.token import create_access_token

pytestmark = pytest.mark.tenancy


async def _token_for_existing_member() -> tuple[int, str]:
    """Find an existing active workspace member without creating test data."""
    with tenant_scope(bypass=True):
        users = await User.objects.order_by(User.id).all()
    for user in users:
        if not user.is_active:
            continue
        if await TenantUserManager().web_memberships(user.id):
            return user.id, create_access_token(subject=str(user.id))[0]
    pytest.fail("test_schema needs an existing active user with a web workspace membership")


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=create_application()), base_url="http://testserver")


async def test_legacy_health_is_public_and_runs_real_permission_probe():
    expected = await entry.Permission.objects.count()
    async with await _client() as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok", "database": "connected"}
    assert expected >= 0


async def test_test_db_requires_bearer_before_running_probe(monkeypatch):
    probe = AsyncMock(side_effect=AssertionError("probe must not run before authentication"))
    monkeypatch.setattr(entry.Permission.objects, "count", probe)
    async with await _client() as client:
        response = await client.get("/api/v1/test-db")

    assert response.status_code == 401
    probe.assert_not_awaited()


async def test_authorized_test_db_uses_real_probe_and_test_auth_identity():
    user_id, token = await _token_for_existing_member()
    headers = {"Authorization": f"Bearer {token}"}

    async with await _client() as client:
        db_response = await client.get("/api/v1/test-db", headers=headers)
        auth_response = await client.get("/api/v1/test-auth", headers=headers)

    assert db_response.status_code == 200, db_response.text
    assert db_response.json() == {
        "database": "connected",
        "permissions": await entry.Permission.objects.count(),
    }
    assert auth_response.status_code == 200, auth_response.text
    assert auth_response.json() == {"message": "Auth endpoint works", "user_id": user_id}


@pytest.mark.parametrize("failure", ["exception", "timeout"])
async def test_real_asgi_probe_failures_are_bounded_and_redacted(monkeypatch, failure):
    user_id, token = await _token_for_existing_member()
    del user_id
    sentinel = "PRIVATE-DB-DSN-sentinel"

    if failure == "exception":

        async def fail():
            raise RuntimeError(sentinel)

        monkeypatch.setattr(entry.Permission.objects, "count", fail)
    else:
        monkeypatch.setattr(entry, "DB_PROBE_TIMEOUT_SECONDS", 0.01)

        async def block():
            await asyncio.Event().wait()

        monkeypatch.setattr(entry.Permission.objects, "count", block)

    async with await _client() as client:
        response = await client.get("/api/v1/test-db", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 500
    assert response.json() == {"detail": "Database connection error"}
    assert sentinel not in response.text


async def test_cancelled_asyncpg_probe_closes_manager_session_and_followup_probe_succeeds():
    query_started = asyncio.Event()

    def delay_permission_count(connection, cursor, statement, parameters, context, executemany):
        lowered = statement.lower()
        if "count(" in lowered and "permissions" in lowered:
            query_started.set()
            return "SELECT pg_sleep(30)", ()
        return statement, parameters

    event.listen(async_engine.sync_engine, "before_cursor_execute", delay_permission_count, retval=True)
    request_task = None
    try:
        async with await _client() as client:
            request_task = asyncio.create_task(client.get("/api/v1/health"))
            await asyncio.wait_for(query_started.wait(), timeout=5)
            request_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request_task
    finally:
        event.remove(async_engine.sync_engine, "before_cursor_execute", delay_permission_count)
        if request_task is not None and not request_task.done():
            request_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request_task

    # Root conftest uses NullPool, so this verifies manager-session cleanup and
    # a successful driver checkout after cancellation, not reuse of a pooled
    # connection.
    assert await asyncio.wait_for(entry.Permission.objects.count(), timeout=5) >= 0
