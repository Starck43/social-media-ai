"""Source-isolated analyze outcome/summary checks; no app, DB or server imports.

Run directly: python tests/test_web_task_outcome_summary.py
Tests the UI projection, not dispatcher notifications or Job persistence.
"""

from __future__ import annotations

import ast
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ERROR_LABEL = "источников с ошибками"
STAGED_LABEL = "ошибок обработки накопленных данных"


def load_summary_functions():
    path = ROOT / "app/web/tasks.py"
    tree = ast.parse(path.read_text())
    functions = {"_plural", "_stat", "_as_int", "_run_outcome", "_job_summary"}
    constants = {"JOB_TYPE_TITLES", "OUTCOME_HEADLINES", "_OUTCOME_LABELS", "_OUTCOME_NOTES"}
    selected = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in functions:
            selected.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in constants for target in node.targets
        ):
            selected.append(node)
    if {node.name for node in selected if isinstance(node, ast.FunctionDef)} != functions:
        raise RuntimeError("Required UI summary definitions absent")
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *selected],
        type_ignores=[],
    )
    namespace = {
        "Any": Any, "datetime": datetime, "timedelta": timedelta, "timezone": timezone,
        "plural": lambda number, one, few, many: one if number == 1 else many,
    }
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace["_run_outcome"], namespace["_job_summary"]


class NoCoercion:
    def __int__(self):
        raise AssertionError("Error counters must not be coerced")

    def __str__(self):
        raise AssertionError("Raw error values must not be exposed")


class WebTaskOutcomeSummaryTests(unittest.TestCase):
    def setUp(self):
        self.outcome, self.summary = load_summary_functions()

    def job(self, result=None, *, status="done", job_type="analyze"):
        return SimpleNamespace(
            status=status, job_type=job_type, result=result,
            error="safe persisted failure", created_at=None, started_at=None,
        )

    def test_reported_source_error_overrides_success(self):
        self.assertEqual(self.outcome("analyze", {"analyzed": 3, "error": 1}), "partial")

    def test_reported_staged_error_overrides_success(self):
        self.assertEqual(self.outcome("analyze", {"analyzed": 3, "staged_errors": 1}), "partial")

    def test_reported_error_overrides_no_data_or_skipped(self):
        for result in ({"analyzed": 0, "error": 1}, {"skipped": 2, "staged_errors": 1}):
            with self.subTest(result=result):
                self.assertEqual(self.outcome("analyze", result), "partial")

    def test_legacy_error_fields_preserve_existing_classification(self):
        for result, expected in (({"analyzed": 2}, "ok"), ({"skipped": 1}, "skipped"), ({}, "no_data")):
            with self.subTest(result=result):
                self.assertEqual(self.outcome("analyze", result), expected)

    def test_known_zero_error_counts_preserve_success(self):
        self.assertEqual(self.outcome("analyze", {"analyzed": 2, "error": 0, "staged_errors": 0}), "ok")

    def test_malformed_error_counters_are_not_coerced_or_displayed(self):
        values = (None, True, False, -1, 1.0, float("inf"), float("nan"), "1", "secret", [], {}, NoCoercion())
        for key in ("error", "staged_errors"):
            for value in values:
                with self.subTest(key=key, kind=type(value).__name__):
                    result = {"analyzed": 2, key: value}
                    self.assertEqual(self.outcome("analyze", result), "ok")
                    summary = self.summary(self.job(result))
                    self.assertFalse(any(tile["label"] in {ERROR_LABEL, STAGED_LABEL} for tile in summary["stats"]))

    def test_one_valid_error_counter_is_not_hidden_by_malformed_other(self):
        for result in ({"analyzed": 2, "error": "bad", "staged_errors": 1},
                       {"analyzed": 2, "error": 1, "staged_errors": True}):
            with self.subTest(result=result):
                self.assertEqual(self.outcome("analyze", result), "partial")

    def test_summary_projects_partial_without_changing_done_status(self):
        job = self.job({"analyzed": 4, "error": 1, "staged_errors": 1, "sources": 2})
        summary = self.summary(job)
        self.assertEqual(summary["status"], "done")
        self.assertEqual(summary["outcome"], "partial")
        self.assertEqual(summary["label"], "Выполнено частично")
        self.assertEqual(summary["headline"]["value"], 4)
        self.assertEqual(job.status, "done")

    def test_error_tiles_are_independent_not_additive(self):
        summary = self.summary(self.job({"analyzed": 4, "error": 2, "staged_errors": 1}))
        counts = {tile["label"]: tile["value"] for tile in summary["stats"]}
        self.assertEqual(counts[ERROR_LABEL], 2)
        self.assertEqual(counts[STAGED_LABEL], 1)
        self.assertIn("пересекаться", summary["outcome_note"])
        self.assertNotIn("ошибок", counts)

    def test_known_zero_tiles_are_not_invented_for_legacy_rows(self):
        legacy = self.summary(self.job({"analyzed": 1}))
        self.assertEqual(len(legacy["stats"]), 3)
        current = self.summary(self.job({"analyzed": 1, "error": 0, "staged_errors": 0}))
        counts = {tile["label"]: tile["value"] for tile in current["stats"]}
        self.assertEqual((counts[ERROR_LABEL], counts[STAGED_LABEL]), (0, 0))

    def test_raw_results_and_exception_text_are_not_added_to_summary(self):
        secret = "synthetic-password-and-private-content"
        result = {"analyzed": 2, "error": 1, "error_messages": [secret],
                  "error_sources": [secret], "per_source": [{"exception": secret}]}
        summary = self.summary(self.job(result))
        self.assertNotIn(secret, repr(summary))
        self.assertNotIn("result", summary)
        self.assertNotIn("error_messages", summary)

    def test_job_and_input_result_are_not_mutated(self):
        job = self.job({"analyzed": 2, "error": 1, "staged_errors": 1, "nested": {"keep": [1]}})
        before = deepcopy(vars(job))
        self.summary(job)
        self.assertEqual(vars(job), before)

    def test_other_job_type_classification_is_unchanged(self):
        cases = (("collect", {"error": 1}, "partial"), ("collect", {"new_items": 0}, "no_data"),
                 ("collect", {"items": 2}, "ok"), ("prune", {"deleted": 0}, "no_data"),
                 ("digest", {"error": 1, "staged_errors": 1}, "ok"))
        for kind, result, expected in cases:
            with self.subTest(kind=kind, result=result):
                self.assertEqual(self.outcome(kind, result), expected)

    def test_pending_running_and_failed_summaries_are_unchanged(self):
        for status in ("pending", "running", "failed"):
            with self.subTest(status=status):
                job = self.job({"analyzed": 2, "error": 1}, status=status)
                summary = self.summary(job)
                self.assertEqual(summary["status"], status)
                self.assertNotIn("outcome", summary)
                self.assertNotIn("stats", summary)
                self.assertEqual(job.status, status)


if __name__ == "__main__":
    unittest.main()
