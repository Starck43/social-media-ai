"""Source-isolated checks for run-now modal per-source chips; no app imports.

Run directly: python tests/test_web_task_source_notes.py
Covers the two row shapes `_source_links` renders: `collect` per-source rows
(top-level `outcome`) and `analyze` per-source rows (row-level analysis flags
plus a nested `collection` row). A failed analysis used to read as
«без новых данных» on a source the run had actually collected items for.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]


def load_source_note_functions():
    path = ROOT / "app/web/task_presenters.py"
    tree = ast.parse(path.read_text())
    functions = {"_plural", "_source_note", "_source_links"}
    selected = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in functions:
            selected.append(node)
    if {node.name for node in selected if isinstance(node, ast.FunctionDef)} != functions:
        raise RuntimeError("Required per-source chip definitions absent")
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *selected],
        type_ignores=[],
    )

    # Same Russian count rules as app/web/deps.plural, inlined so the test
    # stays import-free: 1 результат / 3 результата / 5 результатов.
    def plural(n, one, few, many):
        n = abs(int(n))
        if n % 10 == 1 and n % 100 != 11:
            return one
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return few
        return many

    namespace: dict = {"plural": plural}
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace["_source_note"], namespace["_source_links"]


class WebTaskSourceChipTests(unittest.TestCase):
    def setUp(self):
        self.note, self.links = load_source_note_functions()

    def links_for(self, row):
        return self.links(SimpleNamespace(result={"per_source": [row]}))

    def analyze_row(self, **overrides):
        row = {
            "source_id": 2939,
            "name": "Анна Стрижова",
            "analyzed": 0,
            "actions": 0,
            "raw_total": 17,
            "raw_selected": 17,
            "raw_processed": 0,
            "error": True,
            "staged_error": True,
            "error_code": "failed_output",
            "collection": {"outcome": "collected", "items": 672, "new_items": 650},
        }
        row.update(overrides)
        return row

    def test_failed_analysis_does_not_read_as_no_new_data(self):
        # The exact shape recorded by the run that surfaced the defect.
        self.assertEqual(self.note(self.analyze_row()), "ошибка · failed_output")

    def test_failed_analysis_chip_is_marked_as_error(self):
        links = self.links_for(self.analyze_row())
        self.assertTrue(links[0]["error"])

    def test_collection_error_folded_into_row_is_error_too(self):
        # The handler folds a failed collect stage into the row's error flag.
        row = self.analyze_row(collection={"outcome": "error", "error": True})
        self.assertEqual(self.note(row), "ошибка · failed_output")
        self.assertTrue(self.links_for(row)[0]["error"])

    def test_successful_analysis_result_count_wins(self):
        row = self.analyze_row(error=False, staged_error=False, analyzed=3, raw_processed=3)
        self.assertEqual(self.note(row), "3 результата анализа")
        self.assertFalse(self.links_for(row)[0]["error"])

    def test_analyze_row_without_analysis_speaks_for_its_collect_stage(self):
        row = self.analyze_row(error=False, staged_error=False)
        self.assertEqual(self.note(row), "650 новых")
        self.assertFalse(self.links_for(row)[0]["error"])

    def test_analyze_row_without_collect_stage_stays_no_data(self):
        row = self.analyze_row(error=False, staged_error=False)
        del row["collection"]
        self.assertEqual(self.note(row), "без новых данных")

    def test_malformed_error_flag_is_not_treated_as_failure(self):
        row = self.analyze_row(error="secret", staged_error=None, analyzed=0)
        row["collection"] = {"outcome": "empty"}
        self.assertEqual(self.note(row), "без новых данных")
        self.assertFalse(self.links_for(row)[0]["error"])

    def test_collect_rows_keep_their_existing_notes(self):
        cases = (
            ({"outcome": "collected", "items": 32, "new_items": 32}, "32 новых"),
            ({"outcome": "collected", "items": 32, "new_items": 0}, "новых нет"),
            ({"outcome": "collected", "items": 8}, "8 записей"),
            ({"outcome": "error"}, "ошибка"),
            ({"outcome": "empty"}, "без новых данных"),
        )
        for row, expected in cases:
            with self.subTest(row=row):
                entry = {**row, "source_id": 1, "name": "X"}
                self.assertEqual(self.note(entry), expected)

    def test_collect_row_with_analysis_results_mentions_them(self):
        entry = {"source_id": 1, "name": "X", "outcome": "collected", "items": 5, "new_items": 5, "analyzed": 1}
        self.assertEqual(self.note(entry), "1 результат анализа")


if __name__ == "__main__":
    unittest.main()
