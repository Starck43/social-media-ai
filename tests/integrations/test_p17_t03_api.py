"""API wiring for natural-language 1C queries, export and history with mocked services."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI

from app.api.v1.endpoints import onec
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.onec.export import ExportedOneCFile
from app.services.onec.export import format_result as build_table
from app.services.onec.history import OneCHistoryEntry
from app.services.onec.metadata import OneCEntitySetSpec, OneCFieldSpec
from app.services.onec.nl_query import OneCAnswer, OneCNaturalLanguageError, OneCQueryInterpretation
from app.services.onec.query import MonetaryValue, OneCQueryHistoryMetadata, OneCQueryResult, ValidatedOneCQuery
from app.services.user.auth import get_authenticated_user

PREFIX = "/api/v1/integrations/1c"


def make_result():
    history = OneCQueryHistoryMetadata(
        query_id="q" * 32,
        tenant_id=11,
        actor_id=7,
        connection_id=5,
        entity_set="Catalog_Товары",
        entity_kind="catalog",
        register_function=None,
        plan_fingerprint="a" * 64,
        row_count=1,
        top=100,
        skip=0,
        duration_ms=3,
        from_cache=False,
        executed_at=datetime.now(timezone.utc),
    )
    return OneCQueryResult(
        entity_set="Catalog_Товары",
        entity_kind="catalog",
        register_function=None,
        selected_fields=("Description", "Цена"),
        rows=({"Description": "Товар", "Цена": MonetaryValue(amount=Decimal("10.00"))},),
        row_count=1,
        top=100,
        skip=0,
        next_page_token="next-token",
        plan_fingerprint="a" * 64,
        history_metadata=history,
    )


def make_answer():
    entity = OneCEntitySetSpec(
        name="Catalog_Товары",
        kind="catalog",
        entity_type_name="Catalog_Товары",
        fields={"Description": OneCFieldSpec(name="Description", edm_type="Edm.String")},
        key_fields=(),
    )
    validated = ValidatedOneCQuery(
        entity_spec=entity,
        selected_fields=entity.fields.values(),  # type: ignore[arg-type]
        odata_relative_path="/base/odata/standard.odata/Catalog_Товары?$format=json",
        plan_fingerprint="a" * 64,
        effective_top=100,
        effective_skip=0,
        default_currency="RUB",
    )
    interpretation = OneCQueryInterpretation(
        plan=SimpleNamespace(),
        validated=validated,
        description="Catalog_Товары (catalog): Description",
    )
    result = make_result()
    return OneCAnswer(
        question="покажи товары",
        interpretation=interpretation,
        result=result,
        table=build_table(result),
        history_id=42,
    )


def app_for_test():
    app = FastAPI()
    app.include_router(onec.router, prefix=PREFIX)
    app.dependency_overrides[get_authenticated_user] = lambda: SimpleNamespace(id=7)

    @app.middleware("http")
    async def scope(request, call_next):
        request.state.tenant_id = 11
        return await call_next(request)

    return app


def request(method, path, body=None):
    async def send():
        transport = httpx.ASGITransport(app=app_for_test())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, PREFIX + path, json=body)

    return asyncio.run(send())


def test_query_returns_table_summary_and_history_id(monkeypatch):
    answer = AsyncMock(return_value=make_answer())
    monkeypatch.setattr(onec.OneCQueryAnswerService, "answer", answer)
    response = request("POST", "/query", {"connection_id": 5, "question": "покажи товары"})
    assert response.status_code == 200
    body = response.json()
    assert body["needs_clarification"] is False
    assert body["columns"] == ["Description", "Цена"]
    assert body["rows"][0]["Цена"] == "10,00 ₽"
    assert body["history_id"] == 42
    assert body["next_page_token"] == "next-token"
    assert body["truncated"] is False
    assert body["chart"] == [{"label": "Товар", "value": "10.00"}]
    assert "Catalog_Товары" in body["interpretation"]
    kwargs = answer.await_args.kwargs
    assert kwargs["tenant_id"] == 11 and kwargs["actor_id"] == 7 and kwargs["connection_id"] == 5


def test_query_returns_clarification_without_rows(monkeypatch):
    answer = AsyncMock(return_value=OneCAnswer(question="сколько?", clarification="Уточните период."))
    monkeypatch.setattr(onec.OneCQueryAnswerService, "answer", answer)
    response = request("POST", "/query", {"connection_id": 5, "question": "сколько?"})
    assert response.status_code == 200
    body = response.json()
    assert body["needs_clarification"] is True
    assert body["clarification"] == "Уточните период."
    assert body["rows"] == [] and body["history_id"] is None


def test_query_maps_denied_and_plan_errors(monkeypatch):
    monkeypatch.setattr(onec.OneCQueryAnswerService, "answer", AsyncMock(side_effect=ConnectionDenied()))
    assert request("POST", "/query", {"connection_id": 5, "question": "покажи"}).status_code == 404

    monkeypatch.setattr(
        onec.OneCQueryAnswerService, "answer", AsyncMock(side_effect=OneCNaturalLanguageError("invalid_plan_payload"))
    )
    denied = request("POST", "/query", {"connection_id": 5, "question": "покажи"})
    assert denied.status_code == 400 and denied.json()["detail"] == "invalid_plan_payload"


def test_query_rejects_invalid_body():
    assert request("POST", "/query", {"connection_id": 0, "question": ""}).status_code == 422
    assert request("POST", "/query", {"connection_id": 5, "question": "ok", "extra": 1}).status_code == 422


def test_query_reports_truncation(monkeypatch):
    answer = make_answer()
    answer = OneCAnswer(
        question=answer.question,
        interpretation=answer.interpretation,
        result=answer.result,
        table=replace(answer.table, truncated=True),
        history_id=answer.history_id,
    )
    monkeypatch.setattr(onec.OneCQueryAnswerService, "answer", AsyncMock(return_value=answer))
    body = request("POST", "/query", {"connection_id": 5, "question": "покажи товары"}).json()
    assert body["truncated"] is True


def test_query_continues_from_a_stored_plan_without_a_question(monkeypatch):
    answer = AsyncMock(return_value=make_answer())
    monkeypatch.setattr(onec.OneCQueryAnswerService, "answer", answer)
    response = request("POST", "/query", {"connection_id": 5, "history_id": 42, "page_token": "next-token"})
    assert response.status_code == 200
    assert response.json()["history_id"] == 42
    kwargs = answer.await_args.kwargs
    assert kwargs["history_id"] == 42 and kwargs["page_token"] == "next-token" and kwargs["question"] is None


def test_query_requires_a_question_without_a_stored_plan(monkeypatch):
    answer = AsyncMock(return_value=make_answer())
    monkeypatch.setattr(onec.OneCQueryAnswerService, "answer", answer)
    response = request("POST", "/query", {"connection_id": 5})
    assert response.status_code == 400 and response.json()["detail"] == "question_required"
    answer.assert_not_awaited()


def test_history_lists_actor_entries(monkeypatch):
    entry = OneCHistoryEntry(
        id=1,
        connection_id=5,
        entity_set="Catalog_Товары",
        plan_fingerprint="a" * 64,
        row_count=2,
        duration_ms=8,
        status="executed",
        exported_format=None,
        created_at=datetime(2026, 11, 11, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(onec.OneCQueryHistoryRepository, "list_for_actor", AsyncMock(return_value=(entry,)))
    response = request("GET", "/query/history")
    assert response.status_code == 200
    assert response.json()[0]["entity_set"] == "Catalog_Товары"


def test_export_streams_bytes_with_checksum_headers(monkeypatch):
    file = ExportedOneCFile(
        content=b"PK-bytes",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        extension="xlsx",
        filename="1C_Catalog.xlsx",
        row_count=1,
        sha256="b" * 64,
    )
    export = AsyncMock(return_value=(file, 43))
    monkeypatch.setattr(onec.OneCQueryAnswerService, "export", export)
    response = request("POST", "/query/export", {"connection_id": 5, "format": "xlsx", "history_id": 42})
    assert response.status_code == 200
    assert response.content == b"PK-bytes"
    assert response.headers["x-content-sha256"] == "b" * 64
    assert "1C_Catalog.xlsx" in response.headers["content-disposition"]
    assert export.await_args.kwargs["history_id"] == 42


def test_export_requires_question_or_history_id(monkeypatch):
    monkeypatch.setattr(
        onec.OneCQueryAnswerService, "export", AsyncMock(side_effect=OneCNaturalLanguageError("invalid_question"))
    )
    response = request("POST", "/query/export", {"connection_id": 5, "format": "pdf"})
    assert response.status_code == 400 and response.json()["detail"] == "invalid_question"
