"""HTTP routing for export/send: service and auth are explicit doubles, no DB.

Pins the refusal mapping (404/403/409/422) and the base64 envelope evidence;
service behavior itself is covered by test_p19_t05_delivery_unit.py and the DB
suite test_p19_t05_send_db.py.
"""

import base64
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.engine.interfaces import Dialect

from app.api.v1.endpoints import document_delivery as api
from app.services.communications.attachment_delivery import AttachmentStitchRefused
from app.services.communications.attachments import AttachmentRejected
from app.services.knowledge.documents import DocumentConflict, DocumentUnavailable
from app.services.knowledge.export import ExportedFile

CONTENT = b"synthetic docx bytes"
BODY_HASH = "sha256:" + "a" * 64


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


def exported():
    return ExportedFile(
        content=CONTENT,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        extension="docx",
        revision_id=9,
        content_hash=BODY_HASH,
    )


def scheduled_projection():
    now = datetime.now(timezone.utc)
    return {
        "id": 77,
        "contact_id": 4,
        "author_id": 7,
        "subject": "Счёт",
        "body": "Договор во вложении",
        "content_hash": "sha256:" + "b" * 64,
        "state": "approved",
        "timezone": "UTC",
        "scheduled_at": now + timedelta(minutes=2),
        "approval_expires_at": now + timedelta(days=1),
        "targets": [
            {
                "id": 5,
                "channel": "email",
                "connection_id": 1,
                "identity_id": 2,
                "recipient": "client@example.com",
                "state": "approved",
                "attempts": 0,
                "command_id": None,
                "provider_message_id": None,
                "error_code": None,
            }
        ],
    }


async def call_export(monkeypatch, result=None, error=None, url="/api/v1/documents/3/export/docx"):
    if error is not None:
        monkeypatch.setattr(api, "export_document", AsyncMock(side_effect=error))
    else:
        monkeypatch.setattr(api, "export_document", AsyncMock(return_value=result or exported()))
    async with AsyncClient(transport=ASGITransport(app=app_with_auth_doubles()), base_url="http://test") as client:
        return await client.post(url, json={})


@pytest.mark.asyncio
async def test_export_returns_a_hash_evidenced_envelope(monkeypatch):
    response = await call_export(monkeypatch)

    assert response.status_code == 200
    payload = response.json()
    assert payload["filename"] == "document-3-r9.docx"
    assert payload["media_type"].endswith("wordprocessingml.document")
    assert payload["size"] == len(CONTENT)
    assert payload["revision_id"] == 9
    assert payload["revision_body_hash"] == BODY_HASH
    assert base64.b64decode(payload["content_b64"]) == CONTENT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,status",
    [
        (DocumentUnavailable("document_unavailable"), 404),
        (PermissionError("document_denied"), 403),
        (DocumentConflict("document_stale"), 409),
        (ValueError("invalid_format"), 422),
    ],
)
async def test_export_refusals_map_to_stable_statuses(monkeypatch, error, status):
    response = await call_export(monkeypatch, error=error)

    assert response.status_code == status


@pytest.mark.asyncio
async def test_unknown_format_is_rejected_by_the_route(monkeypatch):
    response = await call_export(monkeypatch, url="/api/v1/documents/3/export/html")

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_send_email_returns_the_scheduled_outbox_item(monkeypatch):
    service = AsyncMock(return_value=scheduled_projection())
    monkeypatch.setattr(api, "send_document_email", service)
    body = {
        "format": "docx",
        "contact_id": 4,
        "subject": "Счёт",
        "body": "Договор во вложении",
        "target": {"channel": "email", "connection_id": 1, "identity_id": 2},
        "scheduled_at": (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
        "ttl_seconds": 3600,
    }

    async with AsyncClient(transport=ASGITransport(app=app_with_auth_doubles()), base_url="http://test") as client:
        response = await client.post("/api/v1/documents/3/send/email", json=body)

    assert response.status_code == 201
    assert response.json()["state"] == "approved"
    assert service.call_args[0][0] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        AttachmentRejected("channel_cannot_attach"),
        AttachmentStitchRefused("revision_changed"),
        DocumentConflict("approval_not_granted"),
    ],
)
async def test_send_refusals_are_conflicts_not_silent_success(monkeypatch, error):
    monkeypatch.setattr(api, "send_document_email", AsyncMock(side_effect=error))
    body = {
        "format": "docx",
        "contact_id": 4,
        "body": "Договор во вложении",
        "target": {"channel": "email", "connection_id": 1, "identity_id": 2},
        "scheduled_at": (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
    }

    async with AsyncClient(transport=ASGITransport(app=app_with_auth_doubles()), base_url="http://test") as client:
        response = await client.post("/api/v1/documents/3/send/email", json=body)

    assert response.status_code == 409
