"""Pure provider cache normalization and USD pricing contracts."""

import unittest
from decimal import Decimal

from app.services.ai.cache_pricing import cache_price_usd, normalize_cache_usage
from app.services.ai.attempt_journal import price_attempt


class CachePricingTests(unittest.TestCase):
    def test_openai_row_10_exact_million_unit_arithmetic(self):
        usage = normalize_cache_usage(
            {"usage": {"prompt_tokens_details": {"cached_tokens": 800_000, "cache_write_tokens": 0}}}, "openai"
        )
        amount = cache_price_usd(
            usage,
            {"input_cost_per_1k": ".12", "cache_read_cost_per_1m": ".024"},
            prompt_tokens=1_000_000,
            pricing_unit=1_000_000,
        )
        self.assertEqual(amount, Decimal("0.0432"))

    def test_openai_all_cached_and_cache_miss(self):
        cached = normalize_cache_usage(
            {"usage": {"prompt_tokens_details": {"cached_tokens": 1_000_000, "cache_write_tokens": 0}}}, "openai"
        )
        miss = normalize_cache_usage(
            {"usage": {"prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}}}, "openai"
        )
        tariff = {"input_cost_per_1k": ".12", "cache_read_cost_per_1m": ".024"}
        self.assertEqual(
            cache_price_usd(cached, tariff, prompt_tokens=1_000_000, pricing_unit=1_000_000), Decimal(".024")
        )
        self.assertEqual(
            cache_price_usd(miss, tariff, prompt_tokens=1_000_000, pricing_unit=1_000_000), Decimal(".12")
        )

    def test_legacy_unit_and_new_million_unit_do_not_cross_scale(self):
        usage = normalize_cache_usage(
            {"usage": {"prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}}}, "openai"
        )
        tariff = {"input_cost_per_1k": ".12", "cache_read_cost_per_1m": ".024"}
        self.assertEqual(cache_price_usd(usage, tariff, prompt_tokens=1000, pricing_unit=1000), Decimal(".12"))
        self.assertEqual(
            cache_price_usd(usage, tariff, prompt_tokens=1000, pricing_unit=1_000_000), Decimal(".00012")
        )

    def test_openai_cached_tokens_are_subtracted_from_inclusive_prompt(self):
        usage = normalize_cache_usage(
            {"usage": {"prompt_tokens_details": {"cached_tokens": 800, "cache_write_tokens": 0}}}, "openai"
        )
        self.assertEqual(
            cache_price_usd(
                usage,
                {"input_cost_per_1k": "0.12", "cache_read_cost_per_1m": "0.024"},
                prompt_tokens=1000,
                pricing_unit=1_000_000,
            ),
            Decimal("0.000043200"),
        )

    def test_openai_missing_cache_count_is_unknown_not_assumed_zero(self):
        usage = normalize_cache_usage({"usage": {}}, "openai")
        self.assertIsNone(
            cache_price_usd(usage, {"input_cost_per_1k": "0.00012"}, prompt_tokens=1000)
        )

    def test_openai_cached_count_cannot_exceed_prompt_total(self):
        usage = normalize_cache_usage(
            {
                "usage": {
                    "prompt_tokens_details": {"cached_tokens": 1001, "cache_write_tokens": 0}
                }
            },
            "openai",
        )
        self.assertIsNone(cache_price_usd(usage, {"input_cost_per_1k": "1"}, prompt_tokens=1000))

    def test_openai_cache_write_without_ttl_price_is_unknown(self):
        usage = normalize_cache_usage(
            {
                "usage": {
                    "prompt_tokens_details": {"cached_tokens": 100, "cache_write_tokens": 50}
                }
            },
            "openai",
        )
        self.assertIsNone(
            cache_price_usd(
                usage,
                {"input_cost_per_1k": "0.1", "cache_read_cost_per_1m": "0.01"},
                prompt_tokens=1000,
            )
        )

    def test_anthropic_ttl_buckets_are_priced_disjointly(self):
        usage = normalize_cache_usage(
            {
                "usage": {
                    "cache_read_input_tokens": 100,
                    "cache_creation_input_tokens": 50,
                    "cache_creation": {
                        "ephemeral_5m_input_tokens": 30,
                        "ephemeral_1h_input_tokens": 20,
                    },
                }
            },
            "anthropic",
        )
        tariff = {
            "input_cost_per_1k": "0.12",
            "cache_read_cost_per_1m": "0.024",
            "cache_write_5m_cost_per_1m": "0.15",
            "cache_write_1h_cost_per_1m": "0.24",
        }
        self.assertEqual(
            cache_price_usd(usage, tariff, prompt_tokens=1000, pricing_unit=1_000_000), Decimal("0.0001317")
        )

    def test_anthropic_aggregate_write_without_ttl_does_not_guess(self):
        usage = normalize_cache_usage(
            {"usage": {"cache_read_input_tokens": 0, "cache_creation_input_tokens": 50}}, "anthropic"
        )
        self.assertIsNone(cache_price_usd(usage, {"input_cost_per_1k": "0.1"}, prompt_tokens=10))

    def test_provider_formats_do_not_cross_interpret_cache_fields(self):
        anthropic = normalize_cache_usage(
            {"usage": {"cache_read_input_tokens": 25}}, "anthropic"
        )
        self.assertIsNone(anthropic["cache_write_tokens"])
        self.assertIsNone(anthropic["cache_write_5m_tokens"])
        unknown = normalize_cache_usage({"usage": {"cached_tokens": 25}}, "custom")
        self.assertEqual(unknown["format"], "unknown")

    def test_v2_sub_nano_usd_is_unresolved_instead_of_rounded_to_zero(self):
        record = {
            "pricing_policy_version": "cache-tariff-v2",
            "prompt_tokens": 40_000,
            "completion_tokens": 0,
            "cache_usage": {
                "format": "openai",
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cache_write_5m_tokens": None,
                "cache_write_1h_tokens": None,
            },
        }
        priced = price_attempt(
            record,
            {
                "id": 4,
                "pricing_token_unit": 1_000_000,
                "input_cost_per_1k": ".00000001",
                "output_cost_per_1k": 0,
                "cache_read_cost_per_1m": 0,
                "cache_write_5m_cost_per_1m": 0,
                "cache_write_1h_cost_per_1m": 0,
            },
        )
        self.assertIsNone(priced["cost_usd"])
        self.assertEqual(priced["cost_certainty"], "undefined")

    def test_v1_sub_nano_rounding_remains_historical(self):
        priced = price_attempt(
            {
                "pricing_policy_version": "provider-price-v1",
                "prompt_tokens": 40,
                "completion_tokens": 0,
            },
            {"id": 4, "input_cost_per_1k": ".00000001", "output_cost_per_1k": 0},
        )
        self.assertEqual(priced["cost_usd"], Decimal(0))
        self.assertEqual(priced["cost_certainty"], "priced")


if __name__ == "__main__":
    unittest.main()
