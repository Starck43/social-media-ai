"""Chat tool wiring for read-only 1C questions; answers are shaped for the agent, never raw."""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agent.tools import get_tool
from app.agent.toolset import onec as onec_tool
from app.core.permissions import set_current_user
from app.core.tenant_context import tenant_scope
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.onec.export import format_result as build_table
from app.services.onec.metadata import OneCEntitySetSpec, OneCFieldSpec
from app.services.onec.nl_query import (
    OneCAnswer,
    OneCNaturalLanguageError,
    OneCQueryInterpretation,
)
from app.services.onec.query import (
    MonetaryValue,
    OneCQueryHistoryMetadata,
    OneCQueryResult,
    ValidatedOneCQuery,
)


def make_answer(rows=1):
    fields = ("Description", "Цена")
    history = OneCQueryHistoryMetadata(
        query_id="q" * 32,
        tenant_id=11,
        actor_id=7,
        connection_id=5,
        entity_set="Catalog_Товары",
        entity_kind="catalog",
        register_function=None,
        plan_fingerprint="a" * 64,
        row_count=rows,
        top=100,
        skip=0,
        duration_ms=4,
        from_cache=False,
        executed_at=datetime.now(timezone.utc),
    )
    result = OneCQueryResult(
        entity_set="Catalog_Товары",
        entity_kind="catalog",
        register_function=None,
        selected_fields=fields,
        rows=tuple(
            {"Description": f"Товар {index}", "Цена": MonetaryValue(amount=Decimal("10.00"))} for index in range(rows)
        ),
        row_count=rows,
        top=100,
        skip=0,
        next_page_token=None,
        plan_fingerprint="a" * 64,
        history_metadata=history,
    )
    entity = OneCEntitySetSpec(
        name="Catalog_Товары",
        kind="catalog",
        entity_type_name="Catalog_Товары",
        fields={"Description": OneCFieldSpec(name="Description", edm_type="Edm.String")},
        key_fields=(),
    )
    validated = ValidatedOneCQuery(
        entity_spec=entity,
        selected_fields=(OneCFieldSpec(name="Description", edm_type="Edm.String"),),
        odata_relative_path="/base/odata/standard.odata/Catalog_Товары?$format=json",
        plan_fingerprint="a" * 64,
        effective_top=100,
        effective_skip=0,
        default_currency="RUB",
    )
    interpretation = OneCQueryInterpretation(
        plan=SimpleNamespace(), validated=validated, description="Catalog_Товары (catalog): Description"
    )
    return OneCAnswer(
        question="покажи товары",
        interpretation=interpretation,
        result=result,
        table=build_table(result),
        history_id=77,
    )


def run_tool(monkeypatch, answer_or_error, *, actor=SimpleNamespace(id=7, is_active=True), tenant=11):
    if isinstance(answer_or_error, Exception):
        monkeypatch.setattr(onec_tool.OneCQueryAnswerService, "answer", AsyncMock(side_effect=answer_or_error))
    else:
        monkeypatch.setattr(onec_tool.OneCQueryAnswerService, "answer", AsyncMock(return_value=answer_or_error))
    token = set_current_user(actor)
    try:
        with tenant_scope(tenant):
            return asyncio.run(onec_tool.onec_query(connection_id=5, question="покажи товары"))
    finally:
        set_current_user(None)
        del token


def test_tool_is_registered_with_source_view_permission():
    spec = get_tool("onec_query")
    assert spec is not None
    assert spec.required_permission == "collection.source.view"
    assert spec.parameters["required"] == ["connection_id", "question"]


def test_tool_requires_authenticated_workspace(monkeypatch):
    monkeypatch.setattr(onec_tool.OneCQueryAnswerService, "answer", AsyncMock())
    result = asyncio.run(onec_tool.onec_query(connection_id=5, question="покажи товары"))
    assert result["code"] == "unauthorized"


def test_tool_returns_interpretation_rows_and_history(monkeypatch):
    result = run_tool(monkeypatch, make_answer(rows=2))
    assert result["needs_clarification"] is False
    assert result["columns"] == ["Description", "Цена"]
    assert result["rows"][0] == {"Description": "Товар 0", "Цена": "10,00 ₽"}
    assert result["row_count"] == 2
    assert result["history_id"] == 77
    assert result["chart"][0]["value"] == "10.00"
    assert result["interpretation"].startswith("Catalog_Товары")


def test_tool_truncates_long_row_sets(monkeypatch):
    result = run_tool(monkeypatch, make_answer(rows=60))
    assert len(result["rows"]) == onec_tool.CHAT_ROW_LIMIT
    assert result["truncated"] is True


def test_tool_returns_clarification(monkeypatch):
    result = run_tool(monkeypatch, OneCAnswer(question="сколько?", clarification="Уточните период."))
    assert result["needs_clarification"] is True
    assert result["clarification"] == "Уточните период."


def test_tool_maps_denied_and_plan_errors(monkeypatch):
    denied = run_tool(monkeypatch, ConnectionDenied())
    assert denied["code"] == "connection_unavailable"

    invalid = run_tool(monkeypatch, OneCNaturalLanguageError("entity_not_allowed"))
    assert invalid["code"] == "entity_not_allowed"
    assert "Некорректные" not in invalid["error"]
