"""Unknown provider spend stays unknown instead of collapsing to a free call.

A claimed-but-unusable provider report (non-USD currency, malformed value), a
broken pricing projection and a transport-side estimate all previously surfaced
as ``cost == 0.0`` and were persisted as zero money downstream. These tests pin
the three-state contract: a real number, an explicit ``0.0`` free call, and
``None`` for unknown spend — from the response projection through
``known_cost_usd`` to the persisted ``agent_messages.cost`` column.

No database, engine or provider request: the manager is driven with async
doubles and the client with an unbound method call against a stand-in.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.models.managers.agent_message_manager import AgentMessageManager, agent_messages
from app.services.ai.llm_client import LLMClient
from app.services.ai.output_contracts import known_cost_usd


def _model(**overrides):
    values = {
        "input_cost_per_1k": 0.5,
        "output_cost_per_1k": 1.0,
        "cache_read_cost_per_1m": 0.024,
        "cache_write_5m_cost_per_1m": None,
        "cache_write_1h_cost_per_1m": None,
        "pricing_token_unit": 1_000_000,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _client(model=None):
    """Lightweight stand-in for an LLM client instance.

    `LLMClient.__init__` asserts an active personal catalog owner, so the usage
    projection is exercised unbound against plain attribute holders instead. The
    sibling helpers the projection calls are bound explicitly because a plain
    namespace has no method binding.
    """
    client = SimpleNamespace(model=model or _model(), provider=SimpleNamespace(api_format="openai", name="dummy"))
    for helper in ("_usage_block", "_usage_block_for_response", "_accounting_cache_usage"):
        setattr(
            client,
            helper,
            (lambda name: lambda *args, **kwargs: getattr(LLMClient, name)(client, *args, **kwargs))(helper),
        )
    return client


def _usage(client, prompt_tokens, completion_tokens, data):
    return LLMClient._usage_block_for_response(client, prompt_tokens, completion_tokens, data)


class TestUsageBlockReportsUnknownSpendAsNone:
    def test_non_usd_provider_report_is_unresolved_not_free(self):
        block = _usage(_client(), 10, 5, {"usage": {"cost": "1.5", "currency": "EUR"}})
        assert block["cost"] is None
        assert block["cost_source"] == "provider_unresolved"
        assert block["cost_is_estimate"] is False
        # The token counts of the paid call survive the unresolved report.
        assert block["total_tokens"] == 15

    def test_unusable_provider_report_is_never_zero_money(self):
        # A claimed total outside the accepted USD range is unresolved spend.
        block = _usage(_client(), 10, 5, {"usage": {"cost": "1000000000"}})
        assert block["cost"] is None
        assert block["cost_source"] == "provider_unresolved"
        assert block["total_tokens"] == 15

    def test_resolvable_reports_keep_their_number(self):
        reported = _usage(_client(), 10, 5, {"usage": {"cost": "1.5"}})
        explicit_zero = _usage(_client(), 10, 5, {"usage": {"cost": 0}})
        assert reported["cost"] == 1.5
        assert reported["cost_source"] == "provider"
        assert explicit_zero["cost"] == 0.0  # a provider-confirmed free call stays 0.0
        assert explicit_zero["cost_source"] == "provider"

    def test_broken_tariff_projection_returns_none_cost(self):
        # The model tariff itself is unusable, so `_usage_block` raises and the
        # exception fallback must not publish 0.0 either.
        client = _client(model=_model(input_cost_per_1k="oops"))
        block = _usage(client, 10, 5, {"usage": {"prompt_tokens": 10, "completion_tokens": 5}})
        assert block["cost"] is None
        assert block["cost_source"] == "undefined"
        assert block["cost_is_estimate"] is True
        assert block["total_tokens"] == 15

    def test_broken_analyze_tariff_projection_returns_none_cost(self):
        # Same fallback behind `_analyze_usage_projection`, reached when the
        # response carries no provider cost report at all.
        client = _client(model=_model(input_cost_per_1k="oops"))
        block = LLMClient._analyze_usage_projection(client, 10, 5, {"content": "ok"})
        assert block["cost"] is None
        assert block["cost_source"] == "undefined"
        assert block["cost_is_estimate"] is True
        assert block["total_tokens"] == 15


class TestKnownCostUsdRejectsEstimates:
    def test_transport_estimate_is_not_provider_spend(self):
        assert known_cost_usd({"usage": {"cost": 0.002, "cost_is_estimate": True}}) is None
        assert known_cost_usd({"usage": {"cost": None, "cost_is_estimate": True}}) is None

    def test_reported_numbers_pass_through(self):
        assert known_cost_usd({"usage": {"cost": 0.5, "cost_is_estimate": False}}) == 0.5
        assert known_cost_usd({"usage": {"cost": 0}}) == 0.0  # explicit free call
        assert known_cost_usd({"usage": {"cost": 0, "cost_is_estimate": False}}) == 0.0

    def test_missing_or_non_numeric_cost_is_unknown(self):
        for usage in (
            {"usage": {}},
            {"usage": {"cost": None}},
            {"usage": {"cost": "0.5"}},
            {"usage": {"cost": True}},
            {"usage": {"cost": -0.5}},
            {"usage": {"cost": float("nan")}},
            {"usage": {"cost": float("inf")}},
            {},
            {"usage": "malformed"},
            None,
        ):
            assert known_cost_usd(usage) is None

    def test_unresolved_provider_block_stays_unknown_end_to_end(self):
        block = _usage(_client(), 10, 5, {"usage": {"cost": "1.5", "currency": "EUR"}})
        assert known_cost_usd({"usage": block}) is None


class TestRecordUsagePersistsUnknownCostAsNull:
    def _manager(self, latest_row):
        """Manager whose latest-turn lookup resolves without a session."""
        manager = AgentMessageManager()
        manager.filter = Mock(
            return_value=SimpleNamespace(
                order_by=lambda *clauses: SimpleNamespace(limit=lambda value: _AwaitedRows(latest_row))
            )
        )
        manager.update_by_id = AsyncMock()
        return manager

    async def test_unknown_cost_is_persisted_as_none(self):
        manager = self._manager([SimpleNamespace(id=42)])
        await manager.record_usage(session_id=7, usage={"total_tokens": 5, "cost": None})
        manager.update_by_id.assert_awaited_once_with(42, tokens=5, cost=None)
        assert manager.update_by_id.await_args.kwargs["cost"] is None

    async def test_explicit_zero_cost_is_persisted_as_zero(self):
        manager = self._manager([SimpleNamespace(id=42)])
        await manager.record_usage(session_id=7, usage={"total_tokens": 5, "cost": 0})
        manager.update_by_id.assert_awaited_once_with(42, tokens=5, cost=0.0)
        assert manager.update_by_id.await_args.kwargs["cost"] == 0.0

    async def test_reported_cost_is_persisted_as_float(self):
        manager = self._manager([SimpleNamespace(id=42)])
        await manager.record_usage(session_id=7, usage={"total_tokens": 5, "cost": 0.25})
        assert manager.update_by_id.await_args.kwargs == {"tokens": 5, "cost": 0.25}

    async def test_unresolved_price_without_tokens_writes_nothing(self):
        # With no tokens and no resolved cost there is nothing to attribute, so
        # the latest turn stays untouched.
        manager = self._manager([SimpleNamespace(id=9)])
        await manager.record_usage(session_id=3, usage={"total_tokens": 0, "cost": None})
        manager.update_by_id.assert_not_awaited()

    async def test_unknown_cost_with_tokens_is_not_skipped(self):
        manager = self._manager([SimpleNamespace(id=9)])
        await manager.record_usage(session_id=3, usage={"total_tokens": 5, "cost": None})
        manager.update_by_id.assert_awaited_once_with(9, tokens=5, cost=None)

    def test_module_manager_is_the_catalog_manager(self):
        # The singleton imported by runtime/web call sites is the class under test.
        assert isinstance(agent_messages, AgentMessageManager)


class _AwaitedRows:
    """Awaitable query tail: `latest = list(await qs.order_by(...).limit(1))`."""

    def __init__(self, rows):
        self._rows = rows

    def __await__(self):
        return self._resolve().__await__()

    async def _resolve(self):
        return self._rows
