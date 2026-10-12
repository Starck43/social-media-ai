"""Attempt journal unit contract: builder, pricing and recording seams; no DB.

Run: python tests/test_llm_attempt_journal.py
The module under test is dependency-free at import time (stdlib only), so it
loads isolated; persistence is exercised through injected doubles, not the
real manager.
"""

import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]

journal = load_isolated_source("_attempt_journal_test", ROOT / "app/services/ai/attempt_journal.py")

DAY = date(2026, 10, 12)
PRIVATE = "PRIVATE-CUSTOMER-SECRET"


def envelope(model="m", prompt=120, completion=40, provider="prov"):
    """Mirror of the real client success envelope."""
    return {
        "request": {"model": model, "prompt": "safe-prompt", "provider": provider},
        "response": {"usage": {"prompt_tokens": prompt, "completion_tokens": completion}},
        "parsed": {"analysis": "A measured conclusion"},
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    }


def invalid_output_envelope(**changes):
    """Mirror of the envelope the client returns after output validation fails."""
    base = envelope(**changes)
    base["response"] = {"error": "invalid_structured_output", "usage": base["response"]["usage"]}
    base["parsed"] = {}
    return base


def transport_failure_envelope():
    """Mirror of the timeout envelope: no usage block at all."""
    return {
        "request": {"model": "m", "messages": [{"role": "user", "content": "safe"}]},
        "response": {"error": "timeout"},
        "parsed": {"analysis": "Timeout"},
    }


def build(results=None, summary=None, **kwargs):
    return journal.build_attempt_records(
        results if results is not None else {"text_analysis": envelope()},
        summary,
        tenant_id=7,
        source_id=3,
        analysis_date=DAY,
        **kwargs,
    )


def only(records, stage):
    return next(record for record in records if record["stage"] == stage)


class BuilderTests(unittest.TestCase):
    def test_every_stage_envelope_and_summary_become_one_record(self):
        records = build(
            {
                "text_analysis": envelope(model="text-m"),
                "image_analysis": envelope(model="vision-m"),
                "video_analysis": envelope(model="video-m"),
            },
            envelope(model="summary-m"),
            task_payload={"job_id": 11, "agent_task_id": 5},
        )
        self.assertEqual(
            [r["stage"] for r in records],
            ["text_analysis", "image_analysis", "video_analysis", journal.STAGE_UNIFIED_SUMMARY],
        )
        for record in records:
            self.assertEqual(record["purpose"], journal.PURPOSE_ANALYSIS)
            self.assertEqual(record["source_id"], 3)
            self.assertEqual(record["analysis_date"], DAY)
            self.assertEqual(record["job_id"], 11)
            self.assertEqual(record["agent_task_id"], 5)
            self.assertEqual(record["outcome"], journal.OUTCOME_SUCCEEDED)
        self.assertEqual(records[0]["model_id"], "text-m")
        self.assertEqual(records[0]["provider_name"], "prov")
        self.assertEqual(records[0]["prompt_tokens"], 120)
        self.assertEqual(records[0]["completion_tokens"], 40)

    def test_failed_output_and_transport_keep_their_usage_and_outcome(self):
        records = build({"image_analysis": invalid_output_envelope()}, transport_failure_envelope())
        invalid = only(records, "image_analysis")
        failed = only(records, journal.STAGE_UNIFIED_SUMMARY)
        self.assertEqual(invalid["outcome"], journal.OUTCOME_FAILED_OUTPUT)
        self.assertEqual((invalid["prompt_tokens"], invalid["completion_tokens"]), (120, 40))
        self.assertEqual(failed["outcome"], journal.OUTCOME_FAILED_TRANSPORT)
        self.assertIsNone(failed["prompt_tokens"])
        self.assertIsNone(failed["completion_tokens"])

    def test_envelope_without_usage_or_error_is_unknown(self):
        record = only(build({"text_analysis": {"request": {"model": "m"}, "response": {}}}), "text_analysis")
        self.assertEqual(record["outcome"], journal.OUTCOME_UNKNOWN)
        self.assertIsNone(record["prompt_tokens"])

    def test_envelope_without_usage_block_reads_raw_provider_usage(self):
        record = only(
            build(
                {
                    "text_analysis": {
                        "request": {"model": "m", "provider": "p"},
                        "response": {"usage": {"prompt_tokens": 5, "completion_tokens": 2}},
                        "parsed": {},
                    }
                }
            ),
            "text_analysis",
        )
        self.assertEqual((record["prompt_tokens"], record["completion_tokens"]), (5, 2))

    def test_non_integer_tokens_are_unknown_usage_per_field(self):
        # (prompt, completion) -> (expected prompt_tokens, expected completion_tokens)
        cases = {
            (True, 40): (None, 40),
            (120, True): (120, None),
            (1.0, 40): (None, 40),
            ("120", 40): (None, 40),
            (-1, 40): (None, 40),
            (120, -3): (120, None),
        }
        for (prompt, completion), (expected_prompt, expected_completion) in cases.items():
            with self.subTest(prompt=prompt, completion=completion):
                record = only(
                    build(results={"text_analysis": envelope(prompt=prompt, completion=completion)}),
                    "text_analysis",
                )
                self.assertEqual(record["prompt_tokens"], expected_prompt)
                self.assertEqual(record["completion_tokens"], expected_completion)

    def test_malformed_execution_references_are_not_coerced(self):
        records = build(task_payload={"job_id": "11", "agent_task_id": True})
        record = only(records, "text_analysis")
        self.assertIsNone(record["job_id"])
        self.assertIsNone(record["agent_task_id"])
        records = build(task_payload="not-a-payload")
        self.assertIsNone(only(records, "text_analysis")["job_id"])

    def test_key_is_content_derived_and_stable(self):
        first = only(build(), "text_analysis")
        second = only(build(), "text_analysis")
        self.assertEqual(first["attempt_key"], second["attempt_key"])
        self.assertRegex(first["attempt_key"], r"^[0-9a-f]{64}$")

    def test_key_distinguishes_distinct_attempts(self):
        base = only(build(), "text_analysis")["attempt_key"]
        self.assertNotEqual(
            only(build(results={"text_analysis": envelope(model="other")}), "text_analysis")["attempt_key"], base
        )
        self.assertNotEqual(
            only(build(results={"text_analysis": envelope(prompt=121)}), "text_analysis")["attempt_key"], base
        )
        self.assertNotEqual(
            only(build(results={"text_analysis": envelope()}, task_payload={"job_id": 99}), "text_analysis")[
                "attempt_key"
            ],
            base,
        )
        other_tenant = only(
            journal.build_attempt_records(
                {"text_analysis": envelope()}, None, tenant_id=8, source_id=3, analysis_date=DAY
            ),
            "text_analysis",
        )["attempt_key"]
        self.assertNotEqual(other_tenant, base)
        self.assertNotIn(PRIVATE, base)

    def test_empty_and_malformed_inputs_produce_no_records(self):
        self.assertEqual(build({}, None), [])
        self.assertEqual(build({"text_analysis": None}, None), [])
        self.assertEqual(build([1, 2, 3], "summary"), [])
        self.assertEqual(journal.build_attempt_records(None, None, tenant_id=1, source_id=1), [])


class PricingTests(unittest.TestCase):
    def priced(self, tariff=None, **changes):
        record = dict(only(build(results={"text_analysis": envelope(**changes)}), "text_analysis"))
        return journal.price_attempt(record, tariff)

    def test_positive_tariff_prices_exactly(self):
        tariff = {"id": 4, "input_cost_per_1k": 0.001, "output_cost_per_1k": 0.002}
        priced = self.priced(tariff)
        self.assertEqual(priced["cost_usd"], Decimal("0.000200000"))
        self.assertEqual(priced["cost_certainty"], journal.CERTAINTY_PRICED)
        self.assertEqual(priced["llm_model_row_id"], 4)
        self.assertEqual(priced["input_cost_per_1k"], Decimal("0.001"))
        self.assertEqual(priced["output_cost_per_1k"], Decimal("0.002"))

    def test_zero_tariff_with_usage_is_known_free_not_unknown(self):
        priced = self.priced({"id": 4, "input_cost_per_1k": 0.0, "output_cost_per_1k": 0.0})
        self.assertEqual(priced["cost_usd"], Decimal(0))
        self.assertEqual(priced["cost_certainty"], journal.CERTAINTY_FREE)

    def test_zero_tokens_with_positive_tariff_stays_priced(self):
        priced = self.priced({"id": 4, "input_cost_per_1k": 0.001, "output_cost_per_1k": 0.002}, prompt=0, completion=0)
        self.assertEqual(priced["cost_usd"], Decimal(0))
        self.assertEqual(priced["cost_certainty"], journal.CERTAINTY_PRICED)

    def test_missing_model_row_is_unknown_tariff_never_free(self):
        priced = self.priced(None)
        self.assertIsNone(priced["cost_usd"])
        self.assertEqual(priced["cost_certainty"], journal.CERTAINTY_UNKNOWN_TARIFF)
        self.assertIsNone(priced["input_cost_per_1k"])

    def test_missing_usage_is_unknown_even_with_a_known_tariff(self):
        record = only(build({"text_analysis": transport_failure_envelope()}), "text_analysis")
        priced = journal.price_attempt(record, {"id": 4, "input_cost_per_1k": 0.001, "output_cost_per_1k": 0.002})
        self.assertIsNone(priced["cost_usd"])
        self.assertEqual(priced["cost_certainty"], journal.CERTAINTY_UNKNOWN_USAGE)

    def test_unknown_tariff_wins_over_unknown_usage(self):
        priced = journal.price_attempt(
            only(build({"text_analysis": transport_failure_envelope()}), "text_analysis"), None
        )
        self.assertEqual(priced["cost_certainty"], journal.CERTAINTY_UNKNOWN_TARIFF)
        self.assertIsNone(priced["cost_usd"])

    def test_raw_record_is_not_mutated(self):
        record = only(build(), "text_analysis")
        original = dict(record)
        journal.price_attempt(record, {"id": 4, "input_cost_per_1k": 1.0, "output_cost_per_1k": 1.0})
        self.assertEqual(record, original)


class OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    class FakeJournal:
        def __init__(self, created=True):
            self.created = created
            self.records = []

        async def record_attempt(self, record):
            self.records.append(record)
            return self.created

    async def test_records_every_attempt_and_prices_them(self):
        fake = self.FakeJournal()
        created = await journal.record_analysis_attempts(
            {"text_analysis": envelope(model="a"), "image_analysis": envelope(model="b")},
            envelope(model="c"),
            source=SimpleNamespace(id=3, tenant_id=7),
            analysis_date=DAY,
            journal=fake,
            tariff_resolver=self._resolver(),
        )
        self.assertEqual(created, 3)
        self.assertEqual([r["model_id"] for r in fake.records], ["a", "b", "c"])
        self.assertEqual({r["cost_certainty"] for r in fake.records}, {journal.CERTAINTY_PRICED})
        self.assertEqual(fake.records[0]["cost_usd"], Decimal("0.000200000"))

    async def test_duplicate_ledger_rows_are_not_new_attempts(self):
        fake = self.FakeJournal(created=False)
        created = await journal.record_analysis_attempts(
            {"text_analysis": envelope()},
            None,
            source=SimpleNamespace(id=3, tenant_id=7),
            analysis_date=DAY,
            journal=fake,
            tariff_resolver=self._resolver(),
        )
        self.assertEqual(created, 0)
        self.assertEqual(len(fake.records), 1)

    async def test_nothing_to_record_leaves_the_journal_untouched(self):
        fake = self.FakeJournal()
        resolver = AsyncMock()
        created = await journal.record_analysis_attempts(
            {},
            None,
            source=SimpleNamespace(id=3, tenant_id=7),
            analysis_date=DAY,
            journal=fake,
            tariff_resolver=resolver,
        )
        self.assertEqual(created, 0)
        self.assertEqual(fake.records, [])
        resolver.assert_not_awaited()

    async def test_tariffs_resolve_once_per_unique_model(self):
        resolver = AsyncMock(return_value={"a": {"id": 1, "input_cost_per_1k": 0.001, "output_cost_per_1k": 0.002}})
        await journal.record_analysis_attempts(
            {"text_analysis": envelope(model="a"), "image_analysis": envelope(model="a")},
            None,
            source=SimpleNamespace(id=3, tenant_id=7),
            analysis_date=DAY,
            journal=self.FakeJournal(),
            tariff_resolver=resolver,
        )
        resolver.assert_awaited_once_with({"a"})

    def _resolver(self):
        tariffs = {
            "a": {"id": 1, "input_cost_per_1k": 0.001, "output_cost_per_1k": 0.002},
            "b": {"id": 2, "input_cost_per_1k": 0.001, "output_cost_per_1k": 0.002},
            "c": {"id": 3, "input_cost_per_1k": 0.001, "output_cost_per_1k": 0.002},
        }

        async def resolve(model_ids):
            return {model_id: tariffs.get(model_id) for model_id in model_ids}

        return resolve


if __name__ == "__main__":
    unittest.main()
