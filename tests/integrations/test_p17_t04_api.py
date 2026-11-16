"""API wiring tests for 1C write commands (`/command/preview`, `/command/approve`, `/command/execute`, `/audit-log`)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI

from app.api.v1.endpoints import onec
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.onec.commands import (
    CAPABILITY_CATALOG_UPDATE,
    CAPABILITY_DOCUMENT_CREATE,
    CAPABILITY_DOCUMENT_POST,
    OneCCommandAuditEntry,
    OneCCommandError,
    OneCCommandRecord,
    OneCCommandService,
)
from app.services.user.auth import get_authenticated_user

PREFIX = "/api/v1/integrations/1c"


def _sample_record(*, state: str = "previewed", capability: str = CAPABILITY_DOCUMENT_CREATE) -> OneCCommandRecord:
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    return OneCCommandRecord(
        id=91,
        tenant_id=11,
        actor_id=7,
        connection_id=5,
        capability=capability,
        approval_policy="per_item",
        idempotency_key="cmd-idem-91",
        external_ref="SM1C:0123456789abcdef01234567",
        entity_set="Document_РеализацияТоваровУслуг",
        target_ref="onec:Document_РеализацияТоваровУслуг:new:SM1C:0123456789abcdef01234567",
        target_guid=None,
        expected_data_version="V1=" if capability != CAPABILITY_DOCUMENT_CREATE else None,
        payload_hash="sha256:" + "a" * 64,
        approved_payload_hash="sha256:" + "a" * 64 if state in {"approved", "succeeded"} else None,
        state=state,
        preview_snapshot={
            "summary": "Создание документа Document_РеализацияТоваровУслуг (черновик без автопроведения)",
            "operation": "document_create",
            "diffs": [],
        },
        provider_receipt=(
            "onec:document:Document_РеализацияТоваровУслуг:11111111-2222-3333-4444-555555555555"
            if state == "succeeded"
            else None
        ),
        error_code=None,
        reconcile_hint=None,
        approved_by=7 if state in {"approved", "succeeded"} else None,
        approved_at=now if state in {"approved", "succeeded"} else None,
        approval_expires_at=now + timedelta(minutes=15) if state in {"approved", "succeeded"} else None,
        executed_at=now if state == "succeeded" else None,
        created_at=now,
    )


def _app_for_test() -> FastAPI:
    app = FastAPI()
    app.include_router(onec.router, prefix=PREFIX)
    app.dependency_overrides[get_authenticated_user] = lambda: SimpleNamespace(id=7)

    @app.middleware("http")
    async def scope(request, call_next):
        request.state.tenant_id = 11
        return await call_next(request)

    return app


def _request(method: str, path: str, body: dict | None = None):
    async def send():
        transport = httpx.ASGITransport(app=_app_for_test())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, PREFIX + path, json=body)

    return asyncio.run(send())


def test_command_preview_endpoint_returns_structured_preview(monkeypatch) -> None:
    preview_mock = AsyncMock(return_value=_sample_record(state="previewed"))
    monkeypatch.setattr(OneCCommandService, "preview_command", preview_mock)

    response = _request(
        "POST",
        "/command/preview",
        {
            "connection_id": 5,
            "capability": CAPABILITY_DOCUMENT_CREATE,
            "idempotency_key": "cmd-idem-91",
            "entity_set": "Document_РеализацияТоваровУслуг",
            "fields": {"СуммаДокумента": "15000.00", "Комментарий": "Счет №1"},
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 91
    assert data["state"] == "previewed"
    assert data["capability"] == CAPABILITY_DOCUMENT_CREATE
    assert data["approval_policy"] == "per_item"
    assert data["payload_hash"] == "sha256:" + "a" * 64
    kwargs = preview_mock.await_args.kwargs
    assert kwargs["tenant_id"] == 11
    assert kwargs["actor_id"] == 7
    assert kwargs["connection_id"] == 5


def test_command_approve_endpoint_binds_payload_hash_and_ttl(monkeypatch) -> None:
    approve_mock = AsyncMock(return_value=_sample_record(state="approved", capability=CAPABILITY_CATALOG_UPDATE))
    monkeypatch.setattr(OneCCommandService, "approve_command", approve_mock)

    response = _request(
        "POST",
        "/command/approve",
        {
            "command_id": 91,
            "expected_payload_hash": "sha256:" + "a" * 64,
            "ttl_seconds": 600,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["state"] == "approved"
    assert data["approved_payload_hash"] == "sha256:" + "a" * 64
    kwargs = approve_mock.await_args.kwargs
    assert kwargs["command_id"] == 91
    assert kwargs["ttl_seconds"] == 600


def test_command_execute_endpoint_and_conflict_error_mapping(monkeypatch) -> None:
    exec_mock = AsyncMock(return_value=_sample_record(state="succeeded", capability=CAPABILITY_DOCUMENT_POST))
    monkeypatch.setattr(OneCCommandService, "execute_command", exec_mock)

    ok_resp = _request("POST", "/command/execute", {"command_id": 91})
    assert ok_resp.status_code == 200
    assert ok_resp.json()["state"] == "succeeded"

    # DataVersion mismatch / conflict maps to HTTP 409 with fixed error code
    monkeypatch.setattr(
        OneCCommandService,
        "execute_command",
        AsyncMock(side_effect=OneCCommandError("data_version_conflict")),
    )
    conflict_resp = _request("POST", "/command/execute", {"command_id": 91})
    assert conflict_resp.status_code == 409
    assert conflict_resp.json()["detail"] == "data_version_conflict"

    # Read-only connection attempt maps to HTTP 400 with write_connection_required
    monkeypatch.setattr(
        OneCCommandService,
        "preview_command",
        AsyncMock(side_effect=OneCCommandError("write_connection_required")),
    )
    ro_resp = _request(
        "POST",
        "/command/preview",
        {
            "connection_id": 5,
            "capability": CAPABILITY_DOCUMENT_CREATE,
            "idempotency_key": "ro-1",
            "entity_set": "Document_РеализацияТоваровУслуг",
            "fields": {"СуммаДокумента": "100.00"},
        },
    )
    assert ro_resp.status_code == 400
    assert ro_resp.json()["detail"] == "write_connection_required"

    # Missing connection maps to HTTP 404
    monkeypatch.setattr(
        OneCCommandService,
        "execute_command",
        AsyncMock(side_effect=ConnectionDenied()),
    )
    denied_resp = _request("POST", "/command/execute", {"command_id": 91})
    assert denied_resp.status_code == 404
    assert denied_resp.json()["detail"] == "connection_unavailable"


def test_audit_log_endpoint_returns_entries(monkeypatch) -> None:
    entry = OneCCommandAuditEntry(
        event_id="evt-ok-1",
        command_id=91,
        tenant_id=11,
        actor_id=7,
        action="onec.command_execute",
        outcome="success",
        capability=CAPABILITY_DOCUMENT_CREATE,
        entity_set="Document_РеализацияТоваровУслуг",
        target_ref="onec:Document_РеализацияТоваровУслуг:new:SM1C:0123",
        state="succeeded",
        payload_hash="sha256:" + "a" * 64,
        provider_receipt="onec:document:Document_РеализацияТоваровУслуг:11111111-2222-3333-4444-555555555555",
        reason_code="executed",
        created_at=datetime(2026, 11, 12, 12, 5, tzinfo=timezone.utc),
    )
    audit_mock = AsyncMock(return_value=(entry,))
    monkeypatch.setattr(OneCCommandService, "list_audit_log", audit_mock)

    response = _request("GET", "/audit-log?limit=25")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["action"] == "onec.command_execute"
    assert items[0]["outcome"] == "success"
    assert items[0]["capability"] == CAPABILITY_DOCUMENT_CREATE
    assert audit_mock.await_args.kwargs["limit"] == 25


def test_command_reconcile_endpoint_resolves_unknown_command(monkeypatch) -> None:
    reconcile_mock = AsyncMock(return_value=_sample_record(state="succeeded", capability=CAPABILITY_DOCUMENT_CREATE))
    monkeypatch.setattr(OneCCommandService, "reconcile_command", reconcile_mock)

    response = _request("POST", "/command/reconcile", {"command_id": 91})
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 91
    assert data["state"] == "succeeded"
    kwargs = reconcile_mock.await_args.kwargs
    assert kwargs["tenant_id"] == 11
    assert kwargs["actor_id"] == 7
    assert kwargs["command_id"] == 91

    monkeypatch.setattr(
        OneCCommandService,
        "reconcile_command",
        AsyncMock(side_effect=OneCCommandError("command_state_conflict")),
    )
    conflict_resp = _request("POST", "/command/reconcile", {"command_id": 91})
    assert conflict_resp.status_code == 409
    assert conflict_resp.json()["detail"] == "command_state_conflict"
