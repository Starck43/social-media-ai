"""Chat/analyze usage carries a priced USD cost (input of the daily cap).

AGENT_DAILY_COST_LIMIT checks `daily_cost_today()`, which sums AgentMessage.cost
(agent chat, written by record_usage from usage['cost']) and DigestRun.llm_cost
(digest summaries). Both used to stay 0 because the LLM clients returned token
counts only — the cap never fired. These tests pin the pricing contract: USD,
Decimal math, tariffs from llm_models (never hardcoded), no DB.
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.personal_catalog_context import catalog_execution_scope
from app.services.ai import llm_client as llm_client_module
from app.services.ai.llm_client import (
    AnthropicClient,
    CustomClient,
    OpenAICompatibleClient,
    _model_price_floor,
    price_usage_usd,
)

# Legacy catalog owner seeded by the personal catalog ownership migration.
CATALOG_OWNER_ID = 2


@pytest.fixture(autouse=True)
def _catalog_owner():
    """Clients assert the model belongs to the acting personal catalog."""
    with catalog_execution_scope(CATALOG_OWNER_ID):
        yield


@pytest.fixture(autouse=True)
def _skip_personal_dispatch_revalidation(monkeypatch):
    """Cost tests isolate usage projections from catalog persistence."""
    final_check = AsyncMock()
    monkeypatch.setattr(llm_client_module, "_revalidate_personal_dispatch_before_http", final_check)
    return final_check


def _model(input_cost_per_1k=0.01, output_cost_per_1k=0.02, **extra):
    provider = SimpleNamespace(
        name="Stub",
        base_url="https://stub.invalid/v1",
        get_api_key=lambda: "k",
        auth_header=None,  # mirrors LLMProvider.auth_header default; _headers() falls back to Bearer
        owner_user_id=CATALOG_OWNER_ID,
        **extra.pop("provider", {}),
    )
    return SimpleNamespace(
        provider=provider,
        model_id="stub-model",
        owner_user_id=CATALOG_OWNER_ID,
        max_tokens=512,
        default_temperature=0.3,
        input_cost_per_1k=input_cost_per_1k,
        output_cost_per_1k=output_cost_per_1k,
        **extra,
    )


def _openai_client(**model_kwargs):
    return OpenAICompatibleClient(_model(**model_kwargs))


def _anthropic_client(**model_kwargs):
    return AnthropicClient(_model(**model_kwargs))


def _custom_client(**model_kwargs):
    model_kwargs.setdefault("custom_endpoint_path", "/generate")
    return CustomClient(_model(provider={"api_format": "custom"}, **model_kwargs))


class _Response:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


class _HTTPClient:
    def __init__(self, data):
        self.data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, *_args, **_kwargs):
        return _Response(self.data)


class TestPriceUsageUsd:
    def test_sub_cent_call_keeps_its_fraction(self):
        # 100 tok in @ $0.01/1K + 50 tok out @ $0.02/1K = $0.002 — a value a
        # float-free integer pipeline would have erased.
        assert price_usage_usd(_model(), 100, 50) == 0.002

    def test_zero_tariffs_are_free_not_unknown(self):
        assert price_usage_usd(_model(0.0, 0.0), 1000, 500) == 0.0

    def test_missing_tariff_attributes_default_to_zero(self):
        bare = SimpleNamespace()  # model row without tariff columns
        assert price_usage_usd(bare, 1000, 500) == 0.0

    def test_missing_usage_defaults_to_zero(self):
        assert price_usage_usd(_model(), None, None) == 0.0

    def test_million_unit_catalog_values_are_priced_and_compared_as_usd_per_million(self):
        model = _model(
            input_cost_per_1k=0.12,
            output_cost_per_1k=0.4,
            pricing_token_unit=1_000_000,
            cache_read_cost_per_1m=0.024,
        )
        assert price_usage_usd(model, 1_000_000, 100_000) == 0.16
        assert _model_price_floor(model) == 0.4

    def test_cache_only_rate_is_not_treated_as_free_for_comparison(self):
        model = _model(
            input_cost_per_1k=0,
            output_cost_per_1k=0,
            pricing_token_unit=1_000_000,
            cache_read_cost_per_1m=0.024,
        )
        assert _model_price_floor(model) == 0.024


class TestUsageBlock:
    def test_usage_block_always_carries_cost(self):
        block = _openai_client()._usage_block(100, 50)
        assert block == {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150, "cost": 0.002}

    def test_usage_block_cost_key_present_without_tariffs(self):
        block = _openai_client(input_cost_per_1k=0.0, output_cost_per_1k=0.0)._usage_block(10, 10)
        assert block["cost"] == 0.0  # callers (record_usage, digest) never branch on missing keys


class TestOpenAIParseChat:
    def test_usage_contains_priced_cost(self):
        data = {
            "choices": [{"message": {"content": "hi", "tool_calls": []}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }
        parsed = _openai_client()._parse_chat(data)
        assert parsed["usage"]["cost"] == 0.002
        # Provider-reported total wins over the computed sum (cached tokens etc.)
        assert parsed["usage"]["total_tokens"] == 150

    def test_provider_total_falls_back_to_sum(self):
        data = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},  # no total_tokens
        }
        parsed = _openai_client()._parse_chat(data)
        assert parsed["usage"]["total_tokens"] == 15


class TestAnthropicParseChat:
    def test_usage_maps_input_output_and_prices_cost(self):
        data = {
            "content": [{"type": "text", "text": "yo"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        parsed = _anthropic_client()._parse_chat(data)
        assert parsed["content"] == "yo"
        assert parsed["usage"] == {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "cost": 0.002,
            "cost_source": "flat_tariff_estimate",
            "cost_is_estimate": True,
        }


class TestProviderCostUsageProjection:
    def test_provider_totals_override_chat_tariffs_in_supported_parsers(self):
        openai_data = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 100,
                "prompt_tokens_details": {"cached_tokens": 800},
                "total_cost": "0.123456789",
            },
        }
        anthropic_data = {
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 1000, "output_tokens": 100, "cost": "0.123456789"},
        }
        assert (
            _openai_client(input_cost_per_1k=0, output_cost_per_1k=0)._parse_chat(openai_data)["usage"]["cost"]
            == 0.123456789
        )
        assert (
            _anthropic_client(input_cost_per_1k=1, output_cost_per_1k=1)._parse_chat(anthropic_data)["usage"]["cost"]
            == 0.123456789
        )

    def test_explicit_zero_and_missing_tokens_remain_resolved(self):
        client = _openai_client(input_cost_per_1k=10, output_cost_per_1k=10)
        parsed_zero = client._parse_chat(
            {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "cost": 0},
            }
        )
        parsed_total = client._parse_chat({"choices": [{"message": {"content": "ok"}}], "cost": "0.25"})
        assert parsed_zero["usage"]["cost"] == 0.0
        assert parsed_total["usage"]["cost"] == 0.25

    def test_generated_text_is_ignored_and_tariff_fallback_remains(self):
        client = _openai_client()
        assert client._usage_block_for_response(100, 50, {"content": "cost: 9.99"})["cost"] == 0.002
        assert (
            client._usage_block_for_response(100, 50, {"usage": {"prompt_tokens": 100, "completion_tokens": 50}})[
                "cost"
            ]
            == 0.002
        )

    def test_known_cache_read_uses_provider_usage_and_cache_rate(self):
        client = _openai_client(
            input_cost_per_1k=0.12, output_cost_per_1k=0.4, pricing_token_unit=1_000_000
        )
        client.provider.api_format = "openai"
        client.model.cache_read_cost_per_1m = 0.024
        result = client._usage_block_for_response(
            1000,
            0,
            {
                "usage": {
                    "prompt_tokens_details": {"cached_tokens": 800, "cache_write_tokens": 0}
                }
            },
        )
        assert result["cost"] == 0.0000432
        assert result["cost_source"] == "cache_tariff"
        assert result["cost_is_estimate"] is True

    async def test_all_actual_adapter_chat_and_analyze_calls_use_provider_totals(
        self, _skip_personal_dispatch_revalidation
    ):
        cases = [
            (
                _openai_client(input_cost_per_1k=0, output_cost_per_1k=0),
                {
                    "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_cost": "0.31"},
                },
            ),
            (
                _anthropic_client(input_cost_per_1k=2, output_cost_per_1k=2),
                {
                    "content": [{"type": "text", "text": "{}"}],
                    "usage": {"input_tokens": 10, "output_tokens": 2, "cost": "0.31"},
                },
            ),
            (
                _custom_client(input_cost_per_1k=2, output_cost_per_1k=2),
                {"text": "{}", "usage": {"prompt_tokens": 10, "completion_tokens": 2}, "cost": "0.31"},
            ),
        ]
        for client, data in cases:
            for method in ("chat", "analyze"):
                patches = [
                    patch.object(llm_client_module, "_personal_admission", new=AsyncMock(return_value=(None, None))),
                    patch.object(llm_client_module, "_verify_personal_dispatch_day", new=AsyncMock()),
                    patch.object(llm_client_module, "_observe_personal_outcome", new=AsyncMock()),
                    patch.object(llm_client_module, "_record_llm_usage", new=AsyncMock()),
                    patch.object(llm_client_module.httpx, "AsyncClient", side_effect=lambda: _HTTPClient(data)),
                    patch.object(client, "_rate_limit", new=AsyncMock()),
                ]
                with ExitStack() as stack:
                    for mocked in patches:
                        stack.enter_context(mocked)
                    result = (
                        await client.chat([{"role": "user", "content": "hi"}])
                        if method == "chat"
                        else await client.analyze("hi")
                    )
                assert result["usage"]["cost"] == 0.31

        assert _skip_personal_dispatch_revalidation.await_count == 6

    async def test_chat_parse_failure_and_malformed_usage_keep_valid_provider_total(self):
        client = _openai_client(input_cost_per_1k=10, output_cost_per_1k=10)
        data = {"choices": [], "usage": "malformed", "total_cost": "0.42"}
        patches = [
            patch.object(client, "_rate_limit", new=AsyncMock()),
            patch.object(llm_client_module, "_personal_admission", new=AsyncMock(return_value=(None, None))),
            patch.object(llm_client_module, "_verify_personal_dispatch_day", new=AsyncMock()),
            patch.object(llm_client_module, "_observe_personal_outcome", new=AsyncMock()),
            patch.object(llm_client_module, "_record_llm_usage", new=AsyncMock()),
            patch.object(llm_client_module.httpx, "AsyncClient", side_effect=lambda: _HTTPClient(data)),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            response = await client.chat([{"role": "user", "content": "hi"}])
        assert response["usage"]["cost"] == 0.42

        # A malformed usage object prevents token extraction but cannot hide the
        # provider total from the analyze projection.
        analyze_data = {
            "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
            "usage": "malformed",
            "cost": "0.42",
        }
        with ExitStack() as stack:
            for mocked in (
                patches[0],
                patch.object(llm_client_module, "_personal_admission", new=AsyncMock(return_value=(None, None))),
                patches[2],
                patch.object(llm_client_module, "_observe_personal_outcome", new=AsyncMock()),
                patches[4],
                patch.object(llm_client_module.httpx, "AsyncClient", side_effect=lambda: _HTTPClient(analyze_data)),
            ):
                stack.enter_context(mocked)
            result = await client.analyze("hi")
        assert result["usage"]["cost"] == 0.42
