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


def _manager(latest_row):
    """Manager whose latest-turn lookup resolves without a session."""
    manager = AgentMessageManager()
    manager.filter = Mock(
        return_value=SimpleNamespace(
            order_by=lambda *clauses: SimpleNamespace(limit=lambda value: _AwaitedRows(latest_row))
        )
    )
    manager.update_by_id = AsyncMock()
    return manager


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

    def test_broken_tariff_analyze_projection_without_data_returns_none_cost(self):
        # The legacy adapter path (`data=None`) has no provider block to fall
        # back to, so a broken tariff must not publish a priced 0.0 there.
        client = _client(model=_model(input_cost_per_1k="oops"))
        block = LLMClient._analyze_usage_projection(client, 10, 5, data=None)
        assert block["cost"] is None
        assert block["cost_source"] == "undefined"
        assert block["cost_is_estimate"] is True
        assert block["prompt_tokens"] == 10
        assert block["completion_tokens"] == 5
        assert block["total_tokens"] == 15


class TestFlatTariffAnalyzeProjectionStaysAnEstimate:
    @pytest.mark.parametrize(
        ("input_cost_per_1k", "output_cost_per_1k"),
        [(0.5, 1.0), (0.5, None), (None, 1.0), (None, None)],
        ids=["both-rates", "missing-output-rate", "missing-input-rate", "missing-both-rates"],
    )
    def test_successful_data_none_projection_is_an_estimate(self, input_cost_per_1k, output_cost_per_1k):
        # The legacy adapter path prices with ordinary flat tariffs; whatever
        # number it projects is a local estimate, not provider-confirmed spend.
        client = _client(
            model=_model(
                input_cost_per_1k=input_cost_per_1k,
                output_cost_per_1k=output_cost_per_1k,
                cache_read_cost_per_1m=None,
                cache_write_5m_cost_per_1m=None,
                cache_write_1h_cost_per_1m=None,
            )
        )
        block = LLMClient._analyze_usage_projection(client, 1_000, 500, data=None)
        assert block["cost_is_estimate"] is True
        assert block["cost_source"] == "flat_tariff_estimate"
        assert block["prompt_tokens"] == 1_000
        assert block["completion_tokens"] == 500
        assert block["total_tokens"] == 1_500
        assert known_cost_usd({"usage": block}) is None
        if input_cost_per_1k is None and output_cost_per_1k is None:
            # Only when both rates are missing does the projection collapse to
            # 0.0: the call is unpriced, not a provider-confirmed free call.
            assert block["cost"] == 0.0
        else:
            # One surviving rate still prices its own token share.
            assert block["cost"] > 0

    def test_priced_data_none_projection_keeps_its_number_but_not_as_spend(self):
        client = _client(model=_model(input_cost_per_1k=0.5, output_cost_per_1k=1.0))
        block = LLMClient._analyze_usage_projection(client, 1_000, 500, data=None)
        assert block["cost"] > 0
        assert known_cost_usd({"usage": block}) is None


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
    async def test_unknown_cost_is_persisted_as_none(self):
        manager = _manager([SimpleNamespace(id=42)])
        await manager.record_usage(session_id=7, usage={"total_tokens": 5, "cost": None})
        manager.update_by_id.assert_awaited_once_with(42, tokens=5, cost=None)
        assert manager.update_by_id.await_args.kwargs["cost"] is None

    async def test_explicit_zero_cost_is_persisted_as_zero(self):
        manager = _manager([SimpleNamespace(id=42)])
        await manager.record_usage(session_id=7, usage={"total_tokens": 5, "cost": 0})
        manager.update_by_id.assert_awaited_once_with(42, tokens=5, cost=0.0)
        assert manager.update_by_id.await_args.kwargs["cost"] == 0.0

    async def test_reported_cost_is_persisted_as_float(self):
        manager = _manager([SimpleNamespace(id=42)])
        await manager.record_usage(session_id=7, usage={"total_tokens": 5, "cost": 0.25})
        assert manager.update_by_id.await_args.kwargs == {"tokens": 5, "cost": 0.25}

    async def test_unresolved_price_without_tokens_writes_nothing(self):
        # With no tokens and no resolved cost there is nothing to attribute, so
        # the latest turn stays untouched.
        manager = _manager([SimpleNamespace(id=9)])
        await manager.record_usage(session_id=3, usage={"total_tokens": 0, "cost": None})
        manager.update_by_id.assert_not_awaited()

    async def test_unknown_cost_with_tokens_is_not_skipped(self):
        manager = _manager([SimpleNamespace(id=9)])
        await manager.record_usage(session_id=3, usage={"total_tokens": 5, "cost": None})
        manager.update_by_id.assert_awaited_once_with(9, tokens=5, cost=None)

    def test_module_manager_is_the_catalog_manager(self):
        # The singleton imported by runtime/web call sites is the class under test.
        assert isinstance(agent_messages, AgentMessageManager)

    async def test_positive_estimate_is_persisted_as_none(self):
        # A transport estimate is not provider spend evidence, so it must not
        # reach the ledger as money even though the tokens are recorded.
        manager = _manager([SimpleNamespace(id=42)])
        await manager.record_usage(
            session_id=7,
            usage={"total_tokens": 5, "cost": 0.25, "cost_source": "cache_tariff", "cost_is_estimate": True},
        )
        manager.update_by_id.assert_awaited_once_with(42, tokens=5, cost=None)
        assert manager.update_by_id.await_args.kwargs["cost"] is None

    async def test_zero_estimate_is_persisted_as_none(self):
        # Only an explicit, non-estimated `0.0` means a free call; an estimated
        # zero stays unknown spend and is still attributed with its tokens.
        manager = _manager([SimpleNamespace(id=42)])
        await manager.record_usage(
            session_id=7,
            usage={"total_tokens": 5, "cost": 0.0, "cost_source": "flat_tariff_estimate", "cost_is_estimate": True},
        )
        manager.update_by_id.assert_awaited_once_with(42, tokens=5, cost=None)
        assert manager.update_by_id.await_args.kwargs["cost"] is None

    async def test_provider_reported_values_are_retained(self):
        # Provider-owned numbers arrive with `cost_is_estimate=False`; both a
        # positive total and an explicit free call keep their value.
        for reported, expected in ((0.25, 0.25), (0.0, 0.0)):
            manager = _manager([SimpleNamespace(id=42)])
            await manager.record_usage(
                session_id=7,
                usage={"total_tokens": 5, "cost": reported, "cost_source": "provider", "cost_is_estimate": False},
            )
            manager.update_by_id.assert_awaited_once_with(42, tokens=5, cost=expected)
            assert manager.update_by_id.await_args.kwargs["cost"] == expected


class TestEstimateFlowsFromProducerToPersistedTurn:
    async def test_cache_tariff_estimate_never_becomes_persisted_money(self):
        # OpenAI prompt tokens are inclusive of cached tokens. The response
        # carries an explicit integer zero cache-write counter, but the model has
        # no cache-read rate while cached tokens are present, so the cache tariff
        # path cannot be priced and stays a flat-tariff estimate rather than a
        # provider total.
        client = _client(
            model=_model(
                input_cost_per_1k=0.5,
                output_cost_per_1k=1.0,
                cache_read_cost_per_1m=None,
                cache_write_5m_cost_per_1m=None,
                cache_write_1h_cost_per_1m=None,
            )
        )
        block = _usage(
            client,
            1_000,
            500,
            {
                "usage": {
                    "prompt_tokens": 1_000,
                    "completion_tokens": 500,
                    "prompt_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 0},
                }
            },
        )
        assert block["cost_is_estimate"] is True
        assert block["cost_source"] == "flat_tariff_estimate"
        assert isinstance(block["cost"], float)
        assert block["cost"] > 0
        assert block["prompt_tokens"] == 1_000
        assert block["completion_tokens"] == 500
        assert block["total_tokens"] == 1_500
        assert known_cost_usd({"usage": block}) is None

        manager = _manager([SimpleNamespace(id=42)])
        await manager.record_usage(session_id=7, usage=block)
        manager.update_by_id.assert_awaited_once_with(42, tokens=1_500, cost=None)

    async def test_provider_zero_cost_with_zero_tokens_is_persisted_as_zero(self):
        # A provider-report-shaped block with an explicit cost of 0 and no
        # tokens is a confirmed free call: it stays 0.0 (not unknown) and is
        # still attributed to the latest turn.
        client = _client()
        block = _usage(client, 0, 0, {"usage": {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0}})
        assert block["cost_source"] == "provider"
        assert block["cost_is_estimate"] is False
        assert block["total_tokens"] == 0
        projection = LLMClient._analyze_usage_projection(client, 0, 0, {"usage": {"cost": 0}})
        assert projection["cost"] == 0.0
        assert known_cost_usd({"usage": projection}) == 0.0

        manager = _manager([SimpleNamespace(id=42)])
        await manager.record_usage(session_id=7, usage=projection)
        manager.update_by_id.assert_awaited_once_with(42, tokens=0, cost=0.0)
        assert manager.update_by_id.await_args.kwargs["cost"] == 0.0


class _AwaitedRows:
    """Awaitable query tail: `latest = list(await qs.order_by(...).limit(1))`."""

    def __init__(self, rows):
        self._rows = rows

    def __await__(self):
        return self._resolve().__await__()

    async def _resolve(self):
        return self._rows
