"""Prepared ASGI routing tests; no live DB/provider probe in test requests."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core import health
from app.main import app, create_application


@pytest.mark.parametrize("factory", [lambda: app, create_application], ids=["singleton", "factory"])
@pytest.mark.parametrize("path", ["/health", "/readyz"])
async def test_ready_routes_report_database_failure(factory, path, monkeypatch):
    monkeypatch.setattr(health.Permission.objects, "count", AsyncMock(side_effect=RuntimeError("PRIVATE-DSN")))
    async with AsyncClient(transport=ASGITransport(app=factory()), base_url="http://testserver") as client:
        result = await client.get(path)
    assert result.status_code == 503
    assert result.json()["status"] == "error"
    assert result.json()["database"] == "disconnected"
    assert "PRIVATE" not in result.text
    assert result.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/health", "/readyz"])
async def test_ready_routes_report_success(path, monkeypatch):
    probe = AsyncMock(return_value=0)
    monkeypatch.setattr(health.Permission.objects, "count", probe)
    async with AsyncClient(transport=ASGITransport(app=create_application()), base_url="http://testserver") as client:
        result = await client.get(path)
    assert result.status_code == 200 and result.json()["database"] == "connected"
    probe.assert_awaited_once()


async def test_live_route_does_not_query_database(monkeypatch):
    probe = AsyncMock(side_effect=AssertionError("liveness must not query"))
    monkeypatch.setattr(health.Permission.objects, "count", probe)
    async with AsyncClient(transport=ASGITransport(app=create_application()), base_url="http://testserver") as client:
        result = await client.get("/livez")
    assert result.status_code == 200 and result.json()["status"] == "ok"
    assert "database" not in result.json()
    probe.assert_not_awaited()


async def test_ready_route_timeout(monkeypatch):
    async def blocked():
        await asyncio.Event().wait()
    monkeypatch.setattr(health.Permission.objects, "count", blocked)
    monkeypatch.setattr(health, "DB_PROBE_TIMEOUT_SECONDS", 0.001)
    async with AsyncClient(transport=ASGITransport(app=create_application()), base_url="http://testserver") as client:
        result = await client.get("/readyz")
    assert result.status_code == 503
