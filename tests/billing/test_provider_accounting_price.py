"""Structured provider pricing and tenant-scoped journal statistics.

Run from the repository root:
.venv/bin/python tests/test_provider_accounting_price.py

Actual source, module-local app doubles, real SQLAlchemy expressions, fake reads.
No DB connection, engine, DDL, provider request or private environment is used.
"""

import unittest
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from source_import_isolation import load_isolated_source
from sqlalchemy import Column, DateTime, Integer, MetaData, Numeric, String, Table

ROOT = Path(__file__).resolve().parents[2]
JOURNAL = load_isolated_source("_provider_pricing_journal", ROOT / "app/services/ai/attempt_journal.py")


def record(response=None, **changes):
    envelope = {
        "request": {"model": "m", "dispatch_id": "a" * 32},
        "response": response if response is not None else {},
        "usage": {"prompt_tokens": 120, "completion_tokens": 40, "cost": 0},
    }
    envelope.update(changes)
    return JOURNAL.build_attempt_records(
        {"text_analysis": envelope}, None, tenant_id=7, source_id=3, analysis_date=date(2026, 10, 12)
    )[0]


class ProviderMetadataTests(unittest.TestCase):
    def test_explicit_zero_is_not_lost_to_truthiness_fallback(self):
        self.assertEqual(JOURNAL.provider_reported_cost_usd({"usage": {"cost": 0}, "cost": "3"}), Decimal(0))
        self.assertEqual(JOURNAL.provider_reported_cost_usd({"total_cost": 0, "cost": "3"}), Decimal(0))

    def test_total_fields_have_documented_precedence_over_components(self):
        raw = {"usage": {"total_cost": "0.003", "cost": "0.004", "prompt_cost": "1", "completion_cost": "2"}}
        self.assertEqual(JOURNAL.provider_reported_cost_usd(raw), Decimal("0.003"))

    def test_complete_components_sum_exactly_and_partial_components_do_not_imply_free(self):
        raw = {"usage": {"input_cost": "0.001", "output_cost": "0.002"}}
        self.assertEqual(JOURNAL.provider_reported_cost_usd(raw), Decimal("0.003"))
        self.assertIsNone(JOURNAL.provider_reported_cost_usd({"usage": {"prompt_cost": 0}}))
        self.assertEqual(
            JOURNAL.provider_reported_cost_usd({"usage": {"prompt_cost": 0, "completion_cost": 0}}), Decimal(0)
        )

    def test_invalid_reports_are_not_money(self):
        for value in (None, True, -1, "", "USD 3", "NaN", "Infinity", {}, [], "1000000000"):
            with self.subTest(kind=type(value).__name__):
                self.assertIsNone(JOURNAL.provider_reported_cost_usd({"cost": value}))

    def test_foreign_or_malformed_currency_is_not_assumed_to_be_usd(self):
        for currency in ("EUR", "", None, True):
            with self.subTest(currency=currency):
                self.assertIsNone(JOURNAL.provider_reported_cost_usd({"currency": currency, "cost": 0}))
        self.assertEqual(JOURNAL.provider_reported_cost_usd({"currency": " usd ", "cost": "0.02"}), Decimal("0.02"))

    def test_parsed_model_text_and_legacy_numeric_projection_are_not_cost_reports(self):
        raw = {"choices": [{"message": {"content": '{"cost": 0}'}}], "parsed": {"cost": 0}}
        self.assertIsNone(JOURNAL.provider_reported_cost_usd(raw))
        self.assertNotIn("_reported_cost_usd", record(raw))


class PricingDecisionTests(unittest.TestCase):
    def test_provider_price_arbitrates_without_a_model_tariff_or_known_usage(self):
        priced = JOURNAL.price_attempt(record({"cost": "0.000012345"}, usage={}), None)
        self.assertEqual(priced["cost_usd"], Decimal("0.000012345"))
        self.assertEqual(priced["cost_certainty"], "priced")
        self.assertIsNone(priced["prompt_tokens"])
        self.assertIsNone(priced["input_cost_per_1k"])
        self.assertNotIn("_reported_cost_usd", priced)

    def test_reported_zero_overrides_nonzero_tariffs_and_retains_their_snapshot(self):
        priced = JOURNAL.price_attempt(record({"cost": 0}), {"id": 4, "input_cost_per_1k": 1, "output_cost_per_1k": 2})
        self.assertEqual(priced["cost_usd"], Decimal(0))
        self.assertEqual(priced["cost_certainty"], "free")
        self.assertEqual(priced["input_cost_per_1k"], Decimal(1))
        self.assertEqual(priced["llm_model_row_id"], 4)

    def test_positive_report_overrides_zero_tariffs(self):
        priced = JOURNAL.price_attempt(
            record({"usage": {"total_cost": "0.03"}}), {"id": 4, "input_cost_per_1k": 0, "output_cost_per_1k": 0}
        )
        self.assertEqual(priced["cost_usd"], Decimal("0.030000000"))
        self.assertEqual(priced["cost_certainty"], "priced")

    def test_explicit_table_zero_is_free_while_unknown_counts_stay_unknown(self):
        priced = JOURNAL.price_attempt(record(usage={}), {"id": 4, "input_cost_per_1k": 0, "output_cost_per_1k": 0})
        self.assertEqual(priced["cost_certainty"], "free")
        self.assertEqual(priced["cost_usd"], Decimal(0))
        self.assertIsNone(priced["prompt_tokens"])

    def test_no_report_and_empty_or_invalid_table_is_null_undefined(self):
        for tariff in (None, {}, {"id": 4}, {"id": 4, "input_cost_per_1k": -1, "output_cost_per_1k": 0}):
            with self.subTest(tariff=tariff):
                priced = JOURNAL.price_attempt(record(), tariff)
                self.assertEqual(priced["cost_certainty"], "undefined")
                self.assertIsNone(priced["cost_usd"])

    def test_known_rate_with_missing_usage_does_not_fabricate_actual_cost(self):
        priced = JOURNAL.price_attempt(record(usage={}), {"id": 4, "input_cost_per_1k": 1, "output_cost_per_1k": 2})
        self.assertIsNone(priced["cost_usd"])
        self.assertEqual(priced["cost_certainty"], "undefined")
        self.assertEqual(priced["input_cost_per_1k"], Decimal(1))

    def test_zero_rate_component_does_not_require_fabricating_its_missing_count(self):
        priced = JOURNAL.price_attempt(
            record(usage={"completion_tokens": 40}),
            {"id": 4, "input_cost_per_1k": 0, "output_cost_per_1k": 1},
        )
        self.assertIsNone(priced["prompt_tokens"])
        self.assertEqual(priced["cost_usd"], Decimal("0.040000000"))
        self.assertEqual(priced["cost_certainty"], "priced")

    def test_sub_precision_positive_report_is_priced_even_when_rounded_cost_is_zero(self):
        priced = JOURNAL.price_attempt(record({"cost": "0.0000000004"}), None)
        self.assertEqual(priced["cost_usd"], Decimal(0))
        self.assertEqual(priced["cost_certainty"], "priced")

    def test_out_of_range_report_does_not_fall_back_to_a_free_tariff(self):
        priced = JOURNAL.price_attempt(
            record({"cost": "1000000000"}), {"id": 4, "input_cost_per_1k": 0, "output_cost_per_1k": 0}
        )
        self.assertIsNone(priced["cost_usd"])
        self.assertEqual(priced["cost_certainty"], "undefined")
        self.assertNotIn("_unresolved_cost_report", priced)

    def test_extreme_or_oversized_components_stay_unresolved_not_underflowed_to_free(self):
        for amount in ("1e-1001", "1e1000001"):
            with self.subTest(amount=amount):
                raw = {"usage": {"prompt_cost": amount, "completion_cost": amount}}
                self.assertEqual(JOURNAL.provider_cost_evidence(raw), (True, None))
                priced = JOURNAL.price_attempt(record(raw), {"input_cost_per_1k": 0, "output_cost_per_1k": 0})
                self.assertEqual(priced["cost_certainty"], "undefined")
                self.assertIsNone(priced["cost_usd"])

    def test_foreign_or_unresolved_currency_report_cannot_fall_back_to_free(self):
        for currency in ("EUR", "", None, True):
            for amount in (0, "0.02"):
                with self.subTest(currency=currency, amount=amount):
                    raw = {"currency": currency, "cost": amount}
                    self.assertEqual(JOURNAL.provider_cost_evidence(raw), (True, None))
                    priced = JOURNAL.price_attempt(record(raw), {"input_cost_per_1k": 0, "output_cost_per_1k": 0})
                    self.assertEqual(priced["cost_certainty"], "undefined")
                    self.assertIsNone(priced["cost_usd"])

    def test_partial_component_report_cannot_fall_back_to_free(self):
        for raw in (
            {"usage": {"prompt_cost": "0.02"}},
            {"usage": {"completion_cost": 0}},
            {"usage": {"prompt_cost": "0.02", "completion_cost": "NaN"}},
        ):
            with self.subTest(raw=raw):
                self.assertEqual(JOURNAL.provider_cost_evidence(raw), (True, None))
                priced = JOURNAL.price_attempt(record(raw), {"input_cost_per_1k": 0, "output_cost_per_1k": 0})
                self.assertEqual(priced["cost_certainty"], "undefined")
                self.assertIsNone(priced["cost_usd"])

    def test_report_and_record_are_not_mutated_and_replay_key_is_stable(self):
        raw = {"cost": "0.02"}
        original = record(raw)
        snapshot = dict(original)
        priced = JOURNAL.price_attempt(original, None)
        self.assertEqual(original, snapshot)
        self.assertEqual(raw, {"cost": "0.02"})
        self.assertEqual(priced["attempt_key"], original["attempt_key"])
        self.assertEqual(JOURNAL.CERTAINTY_UNKNOWN_TARIFF, JOURNAL.CERTAINTY_UNDEFINED)
        self.assertEqual(JOURNAL.CERTAINTY_UNKNOWN_USAGE, JOURNAL.CERTAINTY_UNDEFINED)


TABLE = Table(
    "llm_attempts",
    MetaData(),
    Column("tenant_id", Integer),
    Column("cost_certainty", String),
    Column("cost_usd", Numeric),
    Column("created_at", DateTime(timezone=True)),
)
MODEL = SimpleNamespace(**{column.name: column for column in TABLE.columns})


class BaseManagerDouble:
    def __init__(self, model):
        self.model = model
        self.rows = []
        self.tenant_id = 7
        self.execute = AsyncMock(side_effect=lambda statement: SimpleNamespace(all=lambda: list(self.rows)))

    def _tenant_criterion(self):
        if self.tenant_id is None:
            raise RuntimeError("No tenant")
        return [self.model.tenant_id == self.tenant_id]

    @asynccontextmanager
    async def _get_owned_session(self):
        yield SimpleNamespace(execute=self.execute)


MANAGER = load_isolated_source(
    "app.models.managers._provider_pricing_totals",
    ROOT / "app/models/managers/llm_attempt_manager.py",
    imports={
        "app.models.managers.base_manager": SimpleNamespace(BaseManager=BaseManagerDouble),
        "app.models.llm.llm_attempt": SimpleNamespace(LLMAttempt=MODEL),
    },
)


class JournalStatisticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_and_legacy_unknown_rows_are_counted_separately_from_known_money(self):
        manager = MANAGER.LLMAttemptManager()
        manager.rows = [
            ("priced", 2, Decimal("0.3"), 2),
            ("free", 1, Decimal(0), 1),
            ("undefined", 3, None, 0),
            ("unknown_tariff", 4, None, 0),
            ("unknown_usage", 5, None, 0),
        ]
        totals = await manager.accounting_totals()
        self.assertEqual(totals["attempt_count"], 15)
        self.assertEqual(totals["undefined_count"], 12)
        self.assertEqual(totals["free_count"], 1)
        self.assertEqual(totals["priced_count"], 2)
        self.assertEqual(totals["known_cost_usd"], Decimal("0.3"))
        self.assertFalse(totals["all_costs_known"])
        self.assertEqual(totals["scope"], "captured_journal_only")

    async def test_zero_known_subtotal_does_not_mean_the_unknown_attempts_are_free(self):
        manager = MANAGER.LLMAttemptManager()
        manager.rows = [("undefined", 1, None, 0)]
        totals = await manager.accounting_totals()
        self.assertEqual(totals["known_cost_usd"], Decimal(0))
        self.assertEqual(totals["free_count"], 0)
        self.assertEqual(totals["undefined_count"], 1)
        self.assertFalse(totals["all_costs_known"])

    async def test_null_cost_inside_a_known_group_remains_visible_as_undefined(self):
        manager = MANAGER.LLMAttemptManager()
        manager.rows = [("priced", 3, Decimal("0.2"), 2)]
        totals = await manager.accounting_totals()
        self.assertEqual(totals["priced_count"], 2)
        self.assertEqual(totals["undefined_count"], 1)
        self.assertFalse(totals["all_costs_known"])

    async def test_query_binds_tenant_and_utc_day_instead_of_unscoped_projection_totals(self):
        manager = MANAGER.LLMAttemptManager()
        await manager.accounting_totals(date(2026, 10, 12))
        statement = manager.execute.call_args.args[0]
        params = list(statement.compile().params.values())
        self.assertIn(7, params)
        self.assertIn(datetime(2026, 10, 12, tzinfo=timezone.utc), params)
        self.assertIn(datetime(2026, 10, 13, tzinfo=timezone.utc), params)
        self.assertIn("GROUP BY llm_attempts.cost_certainty", str(statement))

    async def test_missing_tenant_fails_before_any_query(self):
        manager = MANAGER.LLMAttemptManager()
        manager.tenant_id = None
        with self.assertRaisesRegex(RuntimeError, "No tenant"):
            await manager.accounting_totals()
        manager.execute.assert_not_awaited()

    async def test_empty_journal_is_explicitly_empty_not_a_claim_about_all_provider_spend(self):
        totals = await MANAGER.LLMAttemptManager().accounting_totals()
        self.assertEqual(totals["attempt_count"], 0)
        self.assertEqual(totals["scope"], "captured_journal_only")
        self.assertTrue(totals["all_costs_known"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
