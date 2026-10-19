"""Isolated legacy handlers and route declarations, not DB/ASGI acceptance.

Execute the actual entry module with real FastAPI routing and explicit doubles
for every application import. No app/settings/ORM import, startup or conftest.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import APIRouter, HTTPException
from fastapi.routing import iter_route_contexts
from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]
SENTINEL = "SYNTHETIC_PRIVATE_DSN_postgresql://fake:fake-secret@invalid/db"
DATA_ROUTERS = {
    "user": "/users",
    "roles": "/users/roles",
    "monitoring": "/monitoring",
    "scenarios": "/ai",
    "notifications": "/notifications",
    "dashboard": "/dashboard",
    "llm_providers": "/llm",
    "llm_models": "/llm",
    "sources": "/sources",
    "tasks": "/tasks",
    "credentials": "/credentials",
}


@pytest.fixture
def entry():
    async def authenticated_user():
        raise AssertionError("Direct-handler tests must not execute authentication")

    class User:
        pass

    endpoints = SimpleNamespace()
    for name in [*DATA_ROUTERS, "auth", "social"]:
        child = APIRouter()

        async def placeholder():
            raise AssertionError("Placeholder endpoint must not execute")

        child.add_api_route(f"/{name}-fixture", placeholder)
        setattr(endpoints, name, SimpleNamespace(router=child))

    probe = AsyncMock(return_value=7)
    module = load_isolated_source(
        "_legacy_api_probe_fixture",
        ROOT / "app/api/v1/entry.py",
        {
            "app.models": SimpleNamespace(Permission=SimpleNamespace(objects=SimpleNamespace(count=probe)), User=User),
            "app.services.user.auth": SimpleNamespace(get_authenticated_user=authenticated_user),
            "app.api.v1.endpoints": endpoints,
        },
    )
    return module


@pytest.mark.parametrize("count", [0, 7])
async def test_success_contracts(entry, count):
    entry.Permission.objects.count.return_value = count
    assert await entry.test_database() == {"database": "connected", "permissions": count}
    assert await entry.health_check() == {"status": "ok", "database": "connected"}
    assert entry.Permission.objects.count.await_count == 2
    assert entry.DB_PROBE_TIMEOUT_SECONDS == 2.0


@pytest.mark.parametrize("handler", ["test_database", "health_check"])
async def test_exception_is_generic(entry, handler):
    entry.Permission.objects.count.side_effect = RuntimeError(SENTINEL)
    with pytest.raises(HTTPException) as raised:
        await getattr(entry, handler)()
    assert raised.value.status_code == 500
    assert raised.value.detail == "Database connection error"
    assert SENTINEL not in json.dumps({"detail": raised.value.detail})
    assert "fake-secret" not in str(raised.value)


@pytest.mark.parametrize("handler", ["test_database", "health_check"])
async def test_synchronous_probe_failure_is_generic(entry, handler):
    entry.Permission.objects.count = Mock(side_effect=RuntimeError(SENTINEL))
    with pytest.raises(HTTPException) as raised:
        await getattr(entry, handler)()
    assert raised.value.status_code == 500
    assert raised.value.detail == "Database connection error"


@pytest.mark.parametrize("handler", ["test_database", "health_check"])
async def test_timeout_cancels_blocked_probe(entry, handler, monkeypatch):
    cancelled = asyncio.Event()

    async def blocked():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    entry.Permission.objects.count = blocked
    monkeypatch.setattr(entry, "DB_PROBE_TIMEOUT_SECONDS", 0.01, raising=False)
    with pytest.raises(HTTPException) as raised:
        # Guard the test itself so removing the production timeout fails promptly.
        await asyncio.wait_for(getattr(entry, handler)(), timeout=1.0)
    assert raised.value.status_code == 500
    assert raised.value.detail == "Database connection error"
    assert cancelled.is_set()


@pytest.mark.parametrize("handler", ["test_database", "health_check"])
async def test_caller_cancellation_propagates_and_cleans_probe(entry, handler):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    entry.Permission.objects.count = blocked
    task = asyncio.create_task(getattr(entry, handler)())
    try:
        await asyncio.wait_for(started.wait(), timeout=1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1.0)
        assert cancelled.is_set()
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


@pytest.mark.parametrize("handler", ["test_database", "health_check"])
async def test_probe_cancellation_is_not_an_http_failure(entry, handler):
    entry.Permission.objects.count.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await getattr(entry, handler)()


def test_legacy_auth_and_schema_declarations_preserved(entry):
    routes = {route.path: route for route in iter_route_contexts(entry.router.routes)}
    auth = entry.get_authenticated_user
    assert [d.call for d in routes["/test-db"].dependant.dependencies] == [auth]
    assert [d.call for d in routes["/test-auth"].dependant.dependencies] == [auth]
    assert routes["/health"].dependant.dependencies == []
    assert routes["/test-db"].include_in_schema is False
    assert routes["/test-auth"].include_in_schema is False
    assert routes["/health"].include_in_schema is True
    for name, prefix in DATA_ROUTERS.items():
        assert [d.call for d in routes[f"{prefix}/{name}-fixture"].dependant.dependencies] == [auth]
    for name in ("auth", "social"):
        assert routes[f"/{name}/{name}-fixture"].dependant.dependencies == []
    entry.Permission.objects.count.assert_not_called()


async def test_auth_success_contract_preserved(entry):
    assert await entry.test_auth(SimpleNamespace(id=42)) == {"message": "Auth endpoint works", "user_id": 42}
    entry.Permission.objects.count.assert_not_called()
