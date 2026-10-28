"""Actual extracted AI helpers with explicit bindings; no app/DB/providers."""

import math
import unittest
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from types import SimpleNamespace as NS

from tests.source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]


def source(name, bindings=None):
    return load_isolated_source("_p11_" + name, ROOT / "app/services/ai" / (name + ".py"), bindings)


class ProjectionTests(unittest.TestCase):
    def test_scope_previous_is_equal_length_and_nonoverlapping(self):
        module = source("report_scope", {"app.types": NS(PeriodType=NS(DAY="day"))})
        scope = module.ReportScope(date(2026, 3, 1), date(2026, 3, 31), source_ids=frozenset({4}), tenant_id=7)
        previous = scope.previous()
        self.assertEqual(previous.days, scope.days)
        self.assertEqual(previous.end, date(2026, 2, 28))
        self.assertEqual((previous.source_ids, previous.tenant_id), (frozenset({4}), 7))
        self.assertEqual(scope.start, date(2026, 3, 1))
        for value in (None, True, math.nan, math.inf, "bad"):
            self.assertIsNone(module.sentiment_bucket(value))
        self.assertEqual([module.sentiment_bucket(v) for v in (0.39, 0.4, 0.6, 0.61)],
                         ["negative", "neutral", "neutral", "positive"])
        self.assertEqual(module.normalize_digest_axis("sentiment"), "themes")
        self.assertEqual(module.normalize_digest_axis("sources"), "sources")

    def test_legacy_cost_projection_does_not_invent_settlement(self):
        module = source("response_cost")
        unknown = module.extract_cost_info({"usage": {"prompt_tokens": 10}})
        self.assertFalse(unknown["has_cost_report"])
        self.assertIsNone(unknown["cost"]["total"])
        self.assertEqual(unknown["tokens"], {"input": 10, "output": 0, "total": 10})
        reported = module.extract_cost_info({"usage": {"input_tokens": 10, "output_tokens": 5, "total_cost": "USD 0.03"}})
        self.assertEqual(reported["cost"]["total"], 0.03)
        self.assertTrue(reported["has_cost_report"])
        self.assertIsNone(module._num(True))
        self.assertFalse(module.extract_cost_info(None)["has_cost_report"])

    def test_stats_metric_unknowns_and_recursive_json_are_preserved(self):
        render = source("analysis_render")
        module = source("analysis_stats", {
            "app.utils.date_parsing": NS(universal_date_parser=lambda value: datetime.fromisoformat(value) if value else None),
            "app.services.ai.analysis_render": render,
        })
        helper = module.AnalysisStatsMixin()
        self.assertEqual(helper._calculate_content_stats([]), {})
        stats = helper._calculate_content_stats([
            {"text": "test", "from_id": 9, "date": "2026-10-01", "views": 10},
            {"text": "other", "from_id": 9, "date": "2026-10-02", "views": 20, "reactions": 0},
        ])
        self.assertEqual((stats["active_users"], stats["messages_count"], stats["total_views"]), (1, 2, 30))
        self.assertEqual(stats["metric_coverage"]["total_reactions"], {"known": 1, "total": 2})
        self.assertEqual(stats["content_date_range"]["span_days"], 1)
        class Kind(Enum):
            A = "a"
        self.assertEqual(helper._make_json_serializable({"date": date(2026, 1, 1), "values": (Kind.A,)}),
                         {"date": "2026-01-01", "values": ["a"]})


if __name__ == "__main__":
    unittest.main()
