"""DB-exempt HTTP behavior for metadata-only document search."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints import documents as api
from app.services.knowledge.document_contracts import DocumentOut

__source_only__ = True


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


def page():
    item = DocumentOut(
        id=10,
        title="Memo",
        document_type="memo",
        body_text="private content",
        counterparty="Acme",
        tags=["tax"],
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
    return {"items": [item], "limit": 50, "next_before_id": None}


@pytest.mark.asyncio
async def test_search_routes_metadata_filters_and_leaves_plain_list_unchanged(monkeypatch):
    monkeypatch.setattr(api.document_service, "list", AsyncMock(return_value=page()))
    monkeypatch.setattr(api.document_search_service, "search", AsyncMock(return_value=page()))
    async with AsyncClient(transport=ASGITransport(app=app_with_auth_doubles()), base_url="http://test") as client:
        listed = await client.get("/api/v1/documents?boundary=cloud")
        assert listed.status_code == 200
        api.document_service.list.assert_awaited_once_with(boundary="cloud", limit=50, before_id=None)

        searched = await client.get(
            "/api/v1/documents",
            params={"q": "Acme", "document_type": "memo", "status": "draft", "tag": "tax", "contact_id": 3},
        )
        assert searched.status_code == 200
        api.document_search_service.search.assert_awaited_once()
        args = api.document_search_service.search.await_args.kwargs
        assert args["q"] == "Acme"
        assert args["document_type"] == "memo"
        assert args["status"] == "draft"
        assert args["tag"] == "tax"
        assert args["contact_id"] == 3


@pytest.mark.asyncio
async def test_content_search_returns_explicit_unavailable_error(monkeypatch):
    search = AsyncMock(return_value=page())
    monkeypatch.setattr(api.document_search_service, "search", search)
    async with AsyncClient(transport=ASGITransport(app=app_with_auth_doubles()), base_url="http://test") as client:
        response = await client.get("/api/v1/documents", params={"q": "private content", "content_search": "true"})
    assert response.status_code == 422
    assert response.json()["code"] == "content_search_unavailable"
    assert "Content search is unavailable" in response.json()["message"]
    search.assert_not_awaited()
