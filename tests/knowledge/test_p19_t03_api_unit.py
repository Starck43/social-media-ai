"""HTTP behavior with authorization and service mocked; no DB access."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.engine.interfaces import Dialect

from app.api.v1.endpoints import documents as api
from app.services.knowledge.documents import DocumentConflict, DocumentUnavailable
from app.services.knowledge.versions import RevisionOut


@pytest.fixture(autouse=True)
def no_db():
    def forbidden(*args, **kwargs):
        raise AssertionError("DB access forbidden")

    event.listen(Dialect, "do_connect", forbidden)
    yield
    event.remove(Dialect, "do_connect", forbidden)


def app_with_auth_doubles():
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")

    async def allow():
        return None

    for route in api.router.routes:
        for dependency in route.dependant.dependencies:
            app.dependency_overrides[dependency.call] = allow
    return app


@pytest.mark.asyncio
async def test_list_history_and_invalid_diff(monkeypatch):
    revision = RevisionOut(
        id=4,
        version=1,
        content_hash="sha256:" + "a" * 64,
        author_user_id=2,
        author="",
        created_at=datetime(2026, 11, 8, tzinfo=timezone.utc),
        change_description=None,
    )
    monkeypatch.setattr(
        api.version_service, "list", AsyncMock(return_value={"items": [revision], "limit": 50, "next_before_id": None})
    )
    async with AsyncClient(transport=ASGITransport(app=app_with_auth_doubles()), base_url="http://test") as client:
        r = await client.get("/api/v1/documents/1/versions?boundary=local_only")
        assert r.status_code == 200 and r.json()["items"][0]["id"] == 4
        r = await client.get("/api/v1/documents/1/versions/diff?from_id=1&to_id=2&granularity=bad")
        assert r.status_code == 422
        api.version_service.list.side_effect = DocumentUnavailable("secret")
        r = await client.get("/api/v1/documents/1/versions?boundary=cloud")
        assert r.status_code == 404 and "secret" not in r.text


@pytest.mark.asyncio
async def test_restore_conflict_and_request_validation(monkeypatch):
    monkeypatch.setattr(api.version_service, "restore", AsyncMock(side_effect=DocumentConflict("secret")))
    payload = {"expected_revision_id": 3, "expected_updated_at": "2026-11-08T00:00:00Z"}
    async with AsyncClient(transport=ASGITransport(app=app_with_auth_doubles()), base_url="http://test") as client:
        r = await client.post("/api/v1/documents/1/restore/2?boundary=local_only", json=payload)
        assert r.status_code == 409 and "secret" not in r.text
        r = await client.post("/api/v1/documents/1/restore/2?boundary=local_only", json={})
        assert r.status_code == 422
