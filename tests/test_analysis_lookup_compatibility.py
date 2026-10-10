"""Standalone stored-JSON lookup contracts; no app/ORM/DB/provider imports.

Owner: python tests/test_analysis_lookup_compatibility.py
Renderer loaded from its pure source. Aggregator/grouping lookup functions are
compiled from their actual AST bodies only; full modules/ORM are not loaded.
"""

import ast
import copy
import importlib.util
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_AI = _ROOT / "app" / "services" / "ai"
_SPEC = importlib.util.spec_from_file_location(
    "_stored_analysis_contract", _AI / "analysis_render.py"
)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError("Cannot load pure analysis renderer")
render = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(render)


def source_functions(filename, names, class_name=None):
    tree = ast.parse((_AI / filename).read_text())
    scope = (
        tree.body
        if class_name is None
        else next(
            node.body
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
    )
    selected = [
        node
        for node in scope
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    if {node.name for node in selected} != set(names):
        raise AssertionError(f"Missing lookup functions in {filename}")
    # Refuse future imports inside extracted functions rather than initialize app.
    if any(
        isinstance(node, (ast.Import, ast.ImportFrom))
        for function in selected
        for node in ast.walk(function)
    ):
        raise AssertionError("Extracted lookup unexpectedly imports infrastructure")
    module = ast.Module(body=selected, type_ignores=[])
    namespace = {"first_analysis_value": render.first_analysis_value}
    exec(compile(ast.fix_missing_locations(module), filename, "exec"), namespace)
    return namespace


grouping = source_functions("grouping.py", ("_text_analysis", "_digest_value"))
reporting = source_functions("reporting.py", ("_digest_value",), "ReportAggregator")


def legacy_lookup(containers, keys):
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in keys:
            value = container.get(key)
            if value is not None and value != "" and value != []:
                return value
    return None


def grouped_value(data, keys=("value",)):
    return grouping["_digest_value"](data, keys)


def reported_value(data, keys=("value",)):
    return reporting["_digest_value"](None, data, keys)


class StoredAnalysisLookupTests(unittest.TestCase):
    def test_container_precedence_before_alias_precedence(self):
        self.assertEqual(
            render.first_analysis_value(
                [{"old": "first-container"}, {"new": "later-container"}], ("new", "old")
            ),
            "first-container",
        )

    def test_field_alias_order_within_a_container(self):
        self.assertEqual(
            render.first_analysis_value([{"new": 1, "old": 2}], ("new", "old")), 1
        )

    def test_empty_values_fall_through(self):
        self.assertEqual(
            render.first_analysis_value(
                [{"a": None, "b": "", "c": []}, {"a": "fallback"}], ("a", "b", "c")
            ),
            "fallback",
        )

    def test_falsey_but_present_values_are_not_filtered(self):
        for value in (0, 0.0, False, {}, (), " "):
            with self.subTest(value=value):
                self.assertIs(
                    render.first_analysis_value(
                        [{"value": value}, {"value": "fallback"}], ("value",)
                    ),
                    value,
                )

    def test_non_dict_containers_are_skipped(self):
        self.assertEqual(
            render.first_analysis_value(
                [None, [], "bad", 1, {"value": "stored"}], ("value",)
            ),
            "stored",
        )

    def test_no_candidate_or_no_keys_returns_none(self):
        self.assertIsNone(render.first_analysis_value([], ("value",)))
        self.assertIsNone(render.first_analysis_value([{"value": 1}], ()))
        self.assertIsNone(render.first_analysis_value([{}], ("value",)))

    def test_lookup_short_circuits_container_iteration(self):
        def containers():
            yield {"value": "stored"}
            raise AssertionError("Must not evaluate lower-priority containers")

        self.assertEqual(
            render.first_analysis_value(containers(), ("value",)), "stored"
        )

    def test_lookup_matches_previous_loop_without_mutation(self):
        values = (None, "", [], 0, False, {}, "stored", ["stored"])
        for value in values:
            data = [{"value": value}, {"alias": "fallback"}]
            before = copy.deepcopy(data)
            with self.subTest(value=value):
                self.assertEqual(
                    render.first_analysis_value(data, ("value", "alias")),
                    legacy_lookup(data, ("value", "alias")),
                )
                self.assertEqual(data, before)

    def test_parsed_overrides_even_with_none_or_empty(self):
        for value in (None, "", [], 0, False):
            data = {"value": "raw", "parsed": {"value": value}}
            with self.subTest(value=value):
                self.assertIs(render.merge_parsed_analysis(data)["value"], value)

    def test_merge_is_shallow_and_preserves_input(self):
        nested = {"parsed": {"value": "not-recursively-flattened"}}
        data = {"raw_only": 1, "value": "raw", "parsed": {"value": nested}}
        before = copy.deepcopy(data)
        result = render.merge_parsed_analysis(data)
        self.assertIs(result["value"], nested)
        self.assertIsNot(result, data)
        self.assertEqual(result["raw_only"], 1)
        self.assertEqual(data, before)

    def test_malformed_parsed_and_container_fallbacks(self):
        for value in (None, [], "bad", 1):
            with self.subTest(value=value):
                self.assertEqual(render.merge_parsed_analysis(value), {})
                self.assertEqual(
                    render.merge_parsed_analysis({"value": "raw", "parsed": value})[
                        "value"
                    ],
                    "raw",
                )

    def test_text_extractor_uses_parsed_override(self):
        data = {
            "multi_llm_analysis": {
                "text_analysis": {"value": "raw", "parsed": {"value": "parsed"}}
            }
        }
        self.assertEqual(render.extract_text_analysis(data)["value"], "parsed")

    def test_renderer_keeps_top_level_first_while_grouping_keeps_text_first(self):
        data = {
            "topics": ["top"],
            "multi_llm_analysis": {
                "text_analysis": {"topics": ["text"], "parsed": {"topics": ["parsed"]}}
            },
        }
        self.assertEqual(render.render_analysis(data)["main_topics"], ["top"])
        self.assertEqual(grouped_value(data, ("topics",)), ["parsed"])
        self.assertEqual(reported_value(data, ("topics",)), ["text"])

    def test_grouping_parsed_and_reporting_raw_contracts_stay_distinct(self):
        data = {
            "value": "top",
            "multi_llm_analysis": {"text_analysis": {"parsed": {"value": "parsed"}}},
        }
        self.assertEqual(grouped_value(data), "parsed")
        self.assertEqual(reported_value(data), "top")

    def test_digest_consumers_keep_unified_legacy_top_order_without_parsed_overlay(
        self,
    ):
        data = {
            "value": "top",
            "unified_summary": {
                "value": "unified",
                "parsed": {"value": "parsed-unified"},
            },
            "ai_analysis": {"value": "legacy"},
        }
        for lookup in (grouped_value, reported_value):
            with self.subTest(lookup=lookup.__name__):
                self.assertEqual(lookup(data), "unified")
                self.assertEqual(
                    lookup({"value": "top", "ai_analysis": {"value": "legacy"}}),
                    "legacy",
                )
                self.assertEqual(lookup({"value": "top"}), "top")
                self.assertIsNone(lookup({}))
                self.assertIsNone(lookup(None))

    def test_renderer_preserves_unknown_vs_verified_zero_and_url_safety(self):
        data = {
            "unified_summary": {
                "parsed": {"summary": "Stored summary.", "sentiment_score": 0}
            },
            "content_statistics": {
                "total_posts": 2,
                "total_reactions": 0,
                "total_comments": 0,
                "metric_coverage": {"total_reactions": {"known": 2, "total": 2}},
            },
            "original_links": ["javascript:bad", "https://example.com/post"],
        }
        result = render.render_analysis(data)
        self.assertEqual(result["sentiment"]["score"], 0)
        self.assertEqual(result["content_statistics"]["total_reactions"], 0)
        self.assertIsNone(result["content_statistics"]["total_comments"])
        self.assertEqual(result["original_links"], ["https://example.com/post"])
        self.assertEqual(result["analysis_summary"], "Stored summary.")

    def test_existing_malformed_shape_policies_are_not_silently_unified(self):
        bad_multi = {"multi_llm_analysis": "bad", "value": "top"}
        for lookup in (grouped_value, reported_value):
            with self.subTest(lookup=lookup.__name__):
                with self.assertRaises(AttributeError):
                    lookup(bad_multi)
        bad_text = {"multi_llm_analysis": {"text_analysis": "bad"}, "value": "top"}
        with self.assertRaises(AttributeError):
            grouped_value(bad_text)
        self.assertEqual(reported_value(bad_text), "top")
        for data in (bad_multi, bad_text):
            self.assertEqual(render.render_analysis(data)["main_topics"], [])

    def test_consumers_import_canonical_lookup_and_renderer_remains_pure(self):
        for filename in ("grouping.py", "reporting.py"):
            tree = ast.parse((_AI / filename).read_text())
            self.assertTrue(
                any(
                    isinstance(node, ast.ImportFrom)
                    and node.module == "app.services.ai.analysis_render"
                    and any(
                        alias.name == "first_analysis_value" for alias in node.names
                    )
                    for node in tree.body
                ),
                filename,
            )
        tree = ast.parse((_AI / "analysis_render.py").read_text())
        self.assertFalse(
            any(
                isinstance(node, ast.ImportFrom)
                and (node.module or "").startswith("app")
                for node in ast.walk(tree)
            )
        )


if __name__ == "__main__":
    unittest.main()
