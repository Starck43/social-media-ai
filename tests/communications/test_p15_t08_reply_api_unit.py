"""Thread-reply endpoint: request carries content only, the server owns provenance (P15-T08)."""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.services.communications.reply import ReplyUnavailable

PAYLOAD = {"thread_id": 3, "inbound_message_id": 4, "subject": "Re: invoice", "body": "Attached as agreed."}
DRAFT = {
    "id": 7,
    "contact_id": 1,
    "author_id": 1,
    "subject": "Re: invoice",
    "body": "Attached as agreed.",
    "content_hash": "sha256:" + "a" * 64,
    "state": "validated",
    "timezone": "UTC",
    "scheduled_at": None,
    "approval_expires_at": None,
    "targets": [],
}


def _app(api):
    from fastapi import FastAPI

    from app.api.v1.conventions import require_business_scope

    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    for route in api.router.routes:
        for dep in route.dependant.dependencies:
            app.dependency_overrides[dep.call] = lambda: None
    app.dependency_overrides[require_business_scope] = lambda: None
    app.dependency_overrides[api.require_idempotency_key] = lambda: "reply-1"
    return app


@pytest.mark.asyncio
async def test_reply_endpoint_creates_draft_from_server_owned_source(monkeypatch):
    from app.api.v1.endpoints import outbox as api

    create = AsyncMock(return_value=DRAFT)
    monkeypatch.setattr(api, "create_reply_draft", create)
    app = _app(api)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
        response = await client.post(
            "/api/v1/communication/outbox/reply",
            json=PAYLOAD,
            headers={"Idempotency-Key": "reply-1"},
        )
    assert response.status_code == 201
    create.assert_awaited_once()
    kwargs = create.await_args.kwargs
    assert (kwargs["thread_id"], kwargs["inbound_message_id"], kwargs["idempotency_key"]) == (3, 4, "reply-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,status",
    [
        ({**PAYLOAD, "thread_id": 0}, 422),
        ({**PAYLOAD, "body": ""}, 422),
        ({**PAYLOAD, "timezone": "Mars/Olympus"}, 422),
        ({**PAYLOAD, "connection_id": 9}, 422),
    ],
)
async def test_reply_request_is_validated_and_ignores_client_routing(monkeypatch, payload, status):
    from app.api.v1.endpoints import outbox as api

    create = AsyncMock(return_value=DRAFT)
    monkeypatch.setattr(api, "create_reply_draft", create)
    app = _app(api)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
        response = await client.post(
            "/api/v1/communication/outbox/reply",
            json=payload,
            headers={"Idempotency-Key": "reply-1"},
        )
    assert response.status_code == status
    # Extra routing input is rejected outright rather than silently ignored:
    # a client cannot choose the sender account or headers.
    if status != 201:
        create.assert_not_awaited()


@pytest.mark.asyncio
async def test_unavailable_source_is_a_safe_404(monkeypatch):
    from app.api.v1.endpoints import outbox as api

    monkeypatch.setattr(api, "create_reply_draft", AsyncMock(side_effect=ReplyUnavailable("inbound 4 missing")))
    app = _app(api)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
        response = await client.post(
            "/api/v1/communication/outbox/reply",
            json=PAYLOAD,
            headers={"Idempotency-Key": "reply-1"},
        )
    assert response.status_code == 404 and "inbound 4 missing" not in response.text
