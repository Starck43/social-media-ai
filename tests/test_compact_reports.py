"""Aggregate-only report boundaries and economical model routing."""

import json
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent.toolset.reports import analytics_chain_detail, analytics_chains, compact_groups, report_period
from app.services.ai.reporting import ReportAggregator


def groups():
    return [
        {
            "key": f"theme-{i}",
            "chain_id": f"chain-{i}",
            "count": 20 - i,
            "avg_sentiment": 0.2,
            "summary_data": "PRIVATE",
            "response_payload": "PRIVATE",
            "entries": [{"date": "2026-10-08", "count": 2, "avg_sentiment": 0.2, "text": "PRIVATE"}],
        }
        for i in range(15)
    ]


@pytest.mark.parametrize("limit,expected", [(10, 10), (2, 2), (0, 15)])
def test_compact_groups_allowlist_and_limits(limit, expected):
    result = compact_groups(groups(), limit)
    assert len(result) == expected
    assert all(set(g) == {"key", "count", "avg_sentiment"} for g in result)
    assert "PRIVATE" not in json.dumps(result)
    assert compact_groups(groups(), 1, chain_keys=True)[0]["key"] == "chain-0"


@pytest.mark.parametrize("limit", [-1, True, "10"])
def test_invalid_compact_limits(limit):
    with pytest.raises(ValueError):
        compact_groups(groups(), limit)


async def test_report_tools_never_expose_raw_rows_or_invoke_digest(monkeypatch):
    call = AsyncMock(return_value={"groups": groups()})
    monkeypatch.setattr(ReportAggregator, "get_grouped_analytics", call)

    async def forbidden(*args, **kwargs):
        raise AssertionError("preview must not build digest or invoke narrative")

    monkeypatch.setattr("app.services.digest.builder.aggregate", forbidden)
    monkeypatch.setattr("app.services.digest.builder._summarize", forbidden)
    report = await report_period(time_breakdown=True, sentiment="negative", media="image")
    assert len(report["groups"]) == 10 and report["total"] == 15
    assert set(report["groups"][0]["entries"][0]) == {"date", "count", "avg_sentiment"}
    assert call.await_args.kwargs["sentiment"] == "negative"
    assert call.await_args.kwargs["media"] == "image"
    chains = await analytics_chains(limit=0)
    assert len(chains["chains"]) == 15 and chains["chains"][0]["key"] == "chain-0"
    detail = await analytics_chain_detail("chain-0", limit=2)
    assert len(detail["groups"]) == 2
    assert call.await_args.kwargs == {"axis": "days", "days": None, "chain_id": "chain-0", "time_breakdown": True}
    assert "PRIVATE" not in json.dumps([report, chains, detail])


async def test_grouped_service_inclusive_window_and_chain_scope(monkeypatch):
    rows = [
        SimpleNamespace(analysis_date=date.today() + timedelta(days=offset), topic_chain_id=chain)
        for offset, chain in [(0, "a"), (-6, "a"), (-7, "a"), (1, "a"), (0, "b")]
    ]
    query = AsyncMock(return_value=rows)
    grouped = AsyncMock(return_value={"groups": []})
    monkeypatch.setattr(ReportAggregator, "_analytics_query", query)
    monkeypatch.setattr("app.services.ai.grouping.group_analytics", grouped)
    await ReportAggregator().get_grouped_analytics(days=7, source_id=9, tenant_id=4, chain_id="a")
    assert grouped.await_args.args[0] == rows[:2]
    query.assert_awaited_once_with(days=7, source_id=9, tenant_id=4)


async def test_agent_chat_requests_default_model(monkeypatch):
    """Auto-select resolves the fleet default, not the cheapest row.

    The cost-efficient strategy minimizes tariffs first, so with 0.00 tariffs
    it outranked the flagged default model and sent the chat to free rows that
    are rate-limited.
    """
    from app.agent import runtime
    from app.models.managers.llm_model_manager import LLMModelManager

    selected = SimpleNamespace(id=123)
    resolve = AsyncMock(return_value=selected)
    fallback = AsyncMock(return_value={"content": "ok"})
    monkeypatch.setattr(LLMModelManager, "resolve_default_model", resolve)
    monkeypatch.setattr("app.services.ai.llm_client.chat_with_fallback", fallback)
    await runtime._chat([], [])
    resolve.assert_awaited_once_with("text")
    assert fallback.await_args.kwargs["preferred_model"] is selected


async def test_tier_filter_wins_over_global_preferred_model(monkeypatch):
    from app.models import LLMModel
    from app.services.ai import llm_client

    def model(id_, caps, cost):
        # Mirror the real row: `model_type` is a capability list and the
        # filters ask the model, never the raw string.
        return SimpleNamespace(
            id=id_,
            model_type=",".join(caps),
            capabilities=list(caps),
            can_handle=lambda cap: cap in caps,
            input_cost_per_1k=cost,
            output_cost_per_1k=cost,
            provider=SimpleNamespace(is_active=True),
            is_default=False,
        )

    banned = model(1, ("image",), 0.001)
    pricey = model(2, ("text",), 10)
    cheap = model(3, ("text",), 0.01)
    monkeypatch.setattr(llm_client, "_llm_models", AsyncMock(return_value=[banned, pricey, cheap]))
    monkeypatch.setattr(llm_client, "_allowed_model_types", AsyncMock(return_value={"text"}))
    invoked = []

    def create(chosen):
        invoked.append(chosen)
        return SimpleNamespace(chat=AsyncMock(return_value={"content": "ok"}))

    monkeypatch.setattr(llm_client.LLMClientFactory, "create", create)
    await llm_client.chat_with_fallback([], preferred_model=banned)
    assert invoked == [cheap]
