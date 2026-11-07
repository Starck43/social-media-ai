"""DB-exempt HTTP contract tests with service and authentication doubles."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.engine.interfaces import Dialect

from app.api.v1.endpoints import documents as api
from app.services.knowledge.document_contracts import DocumentOut
from app.services.knowledge.documents import DocumentConflict, DocumentUnavailable


@pytest.fixture(autouse=True)
def no_db():
    def forbidden(*args, **kwargs):
        raise AssertionError("DB access forbidden")

    event.listen(Dialect, "do_connect", forbidden)
    yield
    event.remove(Dialect, "do_connect", forbidden)


def document():
    return DocumentOut(
        id=10,
        title="Memo",
        document_type="memo",
        body_text="a",
        counterparty="",
        tags=[],
        contact_id=None,
        case_evidence_id=None,
        owner_user_id=2,
        data_boundary="cloud",
        change_description=None,
        current_revision_id=11,
        version=1,
        content_hash="sha256:" + "a" * 64,
        status="draft",
        approval_invalidated=False,
        approved_by=None,
        author_user_id=2,
        updated_at=datetime(2026, 11, 7, tzinfo=timezone.utc),
    )


def app_with_auth_doubles():
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")

    async def allow():
        return None

    for route in api.router.routes:
        if not hasattr(route, "dependant"):
            continue
        for dependency in route.dependant.dependencies:
            app.dependency_overrides[dependency.call] = allow
    return app


@pytest.mark.asyncio
async def test_get_and_validation_never_echo_internal_errors(monkeypatch):
    monkeypatch.setattr(api.document_service, "get", AsyncMock(return_value=document()))
    async with AsyncClient(transport=ASGITransport(app=app_with_auth_doubles()), base_url="http://test") as client:
        response = await client.get("/api/v1/documents/10?boundary=cloud")
        assert response.status_code == 200 and response.json()["id"] == 10
        response = await client.get("/api/v1/documents/0")
        assert response.status_code == 422 and response.json()["code"] == "validation_error"
        api.document_service.get.side_effect = DocumentUnavailable("secret")
        response = await client.get("/api/v1/documents/10")
        assert response.status_code == 404 and "secret" not in response.text
        api.document_service.get.side_effect = PermissionError("secret")
        response = await client.get("/api/v1/documents/10")
        assert response.status_code == 403 and "secret" not in response.text


@pytest.mark.asyncio
async def test_transition_requires_exact_revision_state_and_hash(monkeypatch):
    monkeypatch.setattr(api.document_service, "transition", AsyncMock(side_effect=DocumentConflict("secret")))
    async with AsyncClient(transport=ASGITransport(app=app_with_auth_doubles()), base_url="http://test") as client:
        payload = {
            "expected_revision_id": 11,
            "expected_updated_at": "2026-11-07T00:00:00Z",
            "expected_hash": "sha256:" + "a" * 64,
            "expected_status": "draft",
            "target": "in_review",
        }
        response = await client.post("/api/v1/documents/10/transition", json=payload)
        assert response.status_code == 409 and "secret" not in response.text
        del payload["expected_hash"]
        response = await client.post("/api/v1/documents/10/transition", json=payload)
        assert response.status_code == 422


def test_every_route_has_scope_and_permission_dependencies():
    for route in api.router.routes:
        calls = [dependency.call for dependency in route.dependant.dependencies]
        assert api.require_model_perm and len(calls) >= 2
        assert any(getattr(call, "__name__", "") == "require_business_scope" for call in calls)
        assert any(getattr(call, "__name__", "") == "dependency" for call in calls)
        if route.methods != {"GET"}:
            assert sum(getattr(call, "__name__", "") == "dependency" for call in calls) >= 2
