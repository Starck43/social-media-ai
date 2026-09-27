"""Chat/analyze usage carries a priced USD cost (input of the daily cap).

AGENT_DAILY_COST_LIMIT checks `daily_cost_today()`, which sums AgentMessage.cost
(agent chat, written by record_usage from usage['cost']) and DigestRun.llm_cost
(digest summaries). Both used to stay 0 because the LLM clients returned token
counts only — the cap never fired. These tests pin the pricing contract: USD,
Decimal math, tariffs from llm_models (never hardcoded), no DB.
"""

from types import SimpleNamespace

from app.services.ai.llm_client import AnthropicClient, OpenAICompatibleClient, price_usage_usd


def _model(input_cost_per_1k=0.01, output_cost_per_1k=0.02, **extra):
    provider = SimpleNamespace(
        name="Stub",
        base_url="https://stub.invalid/v1",
        get_api_key=lambda: "k",
        **extra.pop("provider", {}),
    )
    return SimpleNamespace(
        provider=provider,
        model_id="stub-model",
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
        assert parsed["usage"] == {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150, "cost": 0.002}
