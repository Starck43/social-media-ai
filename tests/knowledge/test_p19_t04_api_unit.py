"""HTTP routing tests: service and auth are explicit doubles, no DB or provider."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.engine.interfaces import Dialect

from app.api.v1.endpoints import documents as api
from app.services.knowledge.approvals import ApprovalConflict


@pytest.fixture(autouse=True)
def no_db():
    def forbidden(*args, **kwargs):
        raise AssertionError("DB access forbidden in unit tests")

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


def row(status="pending"):
    now = datetime.now(timezone.utc)
    return {
        "id": 5,
        "document_id": 10,
        "revision_id": 11,
        "revision_hash": "sha256:" + "a" * 64,
        "requester_user_id": 1,
        "approver_user_id": 2,
        "status": status,
        "requested_at": now,
        "responded_at": now if status != "pending" else None,
        "expires_at": now + timedelta(hours=1),
    }


@pytest.mark.asyncio
async def test_request_pending_decision_and_conflict(monkeypatch):
    monkeypatch.setattr(api.approval_service, "request", AsyncMock(return_value=row()))
    monkeypatch.setattr(
        api.approval_service, "pending", AsyncMock(return_value={"items": [row()], "limit": 50, "next_before_id": None})
    )
    monkeypatch.setattr(api.approval_service, "decide", AsyncMock(return_value=row("approved")))
    async with AsyncClient(transport=ASGITransport(app=app_with_auth_doubles()), base_url="https://test.invalid") as c:
        payload = {
            "approver_user_id": 2,
            "expected_revision_id": 11,
            "expected_hash": "sha256:" + "a" * 64,
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "idempotency_key": "request-1",
        }
        assert (await c.post("/api/v1/documents/10/request-approval", json=payload)).status_code == 201
        assert (await c.get("/api/v1/documents/approvals/pending")).json()["items"][0]["id"] == 5
        response = await c.post("/api/v1/documents/10/approvals/5/approve", json={"comment": "private"})
        assert response.status_code == 200 and response.json()["status"] == "approved"
        assert api.approval_service.decide.await_args.args[2] == {"decision": "approved", "comment": "private"}
        api.approval_service.decide.side_effect = ApprovalConflict("secret")
        response = await c.post("/api/v1/documents/10/approvals/5/reject", json={})
        assert response.status_code == 409 and "secret" not in response.text
        assert (await c.post("/api/v1/documents/10/approvals/5/unknown", json={})).status_code == 422
        payload["expires_at"] = "2026-12-01T12:00:00"
        assert (await c.post("/api/v1/documents/10/request-approval", json=payload)).status_code == 422
