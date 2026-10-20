"""Provider totals in analyzer spend projections; isolated, no DB or providers."""

import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import test_partial_analysis_coverage as coverage_fixture


class _Session:
    class _Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    def begin(self):
        return self._Transaction()

    async def close(self):
        return None


class AnalyzerProviderCostTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = coverage_fixture.PartialCoverageTests()
        self.fixture.setUp()
        self.module = self.fixture.module
        self.analyzer = self.fixture.analyzer
        # The storage fixture replaces this method for its unrelated coverage tests.
        del self.analyzer._price_usage
        self.module.LLMModel.objects = SimpleNamespace(
            filter=Mock(return_value=SimpleNamespace(first=AsyncMock(return_value=None)))
        )
        self.module.__isolated_imports__["app.core.database"] = SimpleNamespace(new_session=_Session)

    def result(self, *, amount=None, reported=False, model="model", prompt=100, completion=10):
        result = {
            "request": {"model": model, "provider": "provider", "prompt": "safe"},
            "response": {"usage": {"prompt_tokens": prompt, "completion_tokens": completion}},
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
            "parsed": {"analysis": "A useful conclusion"},
        }
        if reported:
            result["accounting_cost_reported"] = True
            result["accounting_cost_usd"] = amount
        return result

    async def test_client_totals_aggregate_without_token_or_model_lookup(self):
        total = await self.analyzer._price_usage(
            [
                {
                    "model_id": None,
                    "request_tokens": 0,
                    "response_tokens": 0,
                    "accounting_cost_reported": True,
                    "accounting_cost_usd": "0.125",
                },
                {
                    "model_id": "missing",
                    "request_tokens": 0,
                    "response_tokens": 0,
                    "accounting_cost_reported": True,
                    "accounting_cost_usd": "0.375",
                },
            ]
        )
        self.assertEqual(total, Decimal("50.000000"))
        self.module.LLMModel.objects.filter.assert_not_called()

    async def test_unresolved_client_provenance_blocks_raw_fallback(self):
        total = await self.analyzer._price_usage(
            [
                {
                    "model_id": None,
                    "request_tokens": 0,
                    "response_tokens": 0,
                    "accounting_cost_reported": True,
                    "accounting_cost_usd": None,
                    "provider_response": {"cost": "99.00"},
                }
            ]
        )
        self.assertEqual(total, Decimal(0))

    async def test_explicit_provider_zero_is_stored_but_legacy_unknown_stays_null(self):
        provider_zero = self.result(amount="0", reported=True, prompt=None, completion=None)
        await self.analyzer._save_analysis(
            {"text_analysis": provider_zero}, None, self.fixture.source, {}, "platform", self.fixture.scenario
        )
        self.assertEqual(self.fixture.create.await_args.kwargs["estimated_cost"], Decimal(0))

        await self.analyzer._save_analysis(
            {"text_analysis": provider_zero}, None, self.fixture.source, {}, "platform", self.fixture.scenario
        )
        self.assertEqual(self.fixture.update.await_args.kwargs["estimated_cost"], Decimal(0))

        self.fixture.existing = None
        self.fixture.create.reset_mock()
        self.fixture.update.reset_mock()
        legacy_unknown = self.result(prompt=100, completion=10)
        await self.analyzer._save_analysis(
            {"text_analysis": legacy_unknown}, None, self.fixture.source, {}, "platform", self.fixture.scenario
        )
        self.assertIsNone(self.fixture.create.await_args.kwargs["estimated_cost"])

    async def test_zero_report_with_unknown_sibling_does_not_claim_known_zero(self):
        self.module.LLMModel.objects.filter.return_value.first.return_value = None
        await self.analyzer._save_analysis(
            {
                "text_analysis": self.result(amount="0", reported=True),
                "image_analysis": self.result(model="missing-model"),
            },
            None,
            self.fixture.source,
            {},
            "platform",
            self.fixture.scenario,
        )
        self.assertIsNone(self.fixture.create.await_args.kwargs["estimated_cost"])

    async def test_client_total_survives_malformed_usage_containers_and_counts(self):
        malformed_results = []
        scalar_counts = self.result(amount="0.1234", reported=True)
        scalar_counts["response"]["usage"] = {"prompt_tokens": "10", "completion_tokens": True}
        malformed_results.append(scalar_counts)
        malformed_usage = self.result(amount="0.1234", reported=True)
        malformed_usage["response"]["usage"] = "malformed"
        malformed_results.append(malformed_usage)
        malformed_response = self.result(amount="0.1234", reported=True)
        malformed_response["response"] = ["malformed"]
        malformed_results.append(malformed_response)

        for result in malformed_results:
            await self.analyzer._save_analysis(
                {"text_analysis": result}, None, self.fixture.source, {}, "platform", self.fixture.scenario
            )
            persisted = self.fixture.create.await_args or self.fixture.update.await_args
            self.assertEqual(persisted.kwargs["estimated_cost"], Decimal("12.340000"))
            self.fixture.create.reset_mock()
            self.fixture.update.reset_mock()

    async def test_legacy_raw_provider_total_is_used_and_summary_stays_out_of_projection(self):
        text = self.result(reported=False)
        text.pop("usage")
        text["response"]["cost"] = "0.12"
        summary = self.result(amount="0.99", reported=True)
        await self.analyzer._save_analysis(
            {"text_analysis": text}, summary, self.fixture.source, {}, "platform", self.fixture.scenario
        )
        self.assertEqual(self.fixture.create.await_args.kwargs["estimated_cost"], Decimal("12.000000"))


if __name__ == "__main__":
    unittest.main()
