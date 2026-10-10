"""Standalone input contract and source-wiring checks, without app/DB imports.

Owner command: python tests/test_cli_username_parsing.py
Command execution, tenant resolution and live handlers are deliberately excluded.
"""

import ast
import importlib.util
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "_cli_input_contract", _ROOT / "cli" / "_inputs.py"
)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError("Cannot load CLI input helper")
inputs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(inputs)


def legacy_collect_usernames(value):
    return [
        token.strip().lstrip("@")
        for token in value.replace(",", " ").split()
        if token.strip()
    ]


def legacy_analyze_usernames(value):
    return [
        token.strip().lstrip("@")
        for token in (value or "").replace(",", " ").split()
        if token.strip()
    ]


class CliUsernameParsingTests(unittest.TestCase):
    def test_absent_or_empty_input(self):
        for value in (None, "", " ", ",,,", "\t\n, "):
            with self.subTest(value=value):
                self.assertEqual(inputs.parse_usernames(value), [])

    def test_mixed_separators_and_leading_at_signs(self):
        self.assertEqual(
            inputs.parse_usernames("@alice, bob\t@@carol\ndave"),
            ["alice", "bob", "carol", "dave"],
        )

    def test_case_order_and_duplicates_preserved(self):
        self.assertEqual(
            inputs.parse_usernames("@Bob,bob,@Bob,Alice"),
            ["Bob", "bob", "Bob", "Alice"],
        )

    def test_internal_at_sign_and_punctuation_are_not_rewritten(self):
        self.assertEqual(
            inputs.parse_usernames(
                "name@example.org,@name-with-dash,@name_with_underscore"
            ),
            ["name@example.org", "name-with-dash", "name_with_underscore"],
        )

    def test_bare_at_sign_tokens_remain_empty_entries(self):
        self.assertEqual(inputs.parse_usernames("@,@@,@alice"), ["", "", "alice"])

    def test_unicode_text_and_whitespace(self):
        self.assertEqual(
            inputs.parse_usernames("@Пользователь\u2003@名前\u00a0MixedCase"),
            ["Пользователь", "名前", "MixedCase"],
        )

    def test_each_call_returns_an_independent_list(self):
        first = inputs.parse_usernames("@alice")
        first.append("synthetic")
        self.assertEqual(inputs.parse_usernames("@alice"), ["alice"])

    def test_compatibility_with_each_legacy_comprehension(self):
        cases = (
            None,
            "",
            "@",
            "@@",
            "alice",
            "@alice,@bob",
            "@A A @A",
            " , \t ",
            "@名前,\n@Пользователь",
            "name@example.org",
            "@a\u2003@b",
        )
        for value in cases:
            with self.subTest(value=value):
                self.assertEqual(
                    inputs.parse_usernames(value), legacy_analyze_usernames(value)
                )
                if value:
                    self.assertEqual(
                        inputs.parse_usernames(value), legacy_collect_usernames(value)
                    )

    def test_all_three_command_sites_use_the_shared_helper(self):
        expected = {"collect": ["monitored", "excluded"], "direct": ["excluded"]}
        for module, arguments in expected.items():
            tree = ast.parse((_ROOT / "cli" / "commands" / f"{module}.py").read_text())
            with self.subTest(module=module):
                self.assertTrue(
                    any(
                        isinstance(node, ast.ImportFrom)
                        and node.module == "cli._inputs"
                        and any(name.name == "parse_usernames" for name in node.names)
                        for node in tree.body
                    )
                )
                calls = [
                    node
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "parse_usernames"
                ]
                self.assertEqual(
                    sorted(ast.unparse(call.args[0]) for call in calls),
                    sorted(arguments),
                )
                self.assertTrue(
                    all(len(call.args) == 1 and not call.keywords for call in calls)
                )

    def test_collect_payload_guards_remain_distinct_from_analyze(self):
        collect = ast.parse((_ROOT / "cli" / "commands" / "collect.py").read_text())
        for argument, key in (
            ("monitored", "monitored_users"),
            ("excluded", "excluded_users"),
        ):
            with self.subTest(argument=argument):
                guard = next(
                    node
                    for node in ast.walk(collect)
                    if isinstance(node, ast.If)
                    and isinstance(node.test, ast.Name)
                    and node.test.id == argument
                )
                assignment = guard.body[0]
                self.assertIsInstance(assignment, ast.Assign)
                self.assertEqual(
                    ast.unparse(assignment.targets[0]), f"payload['{key}']"
                )
                self.assertEqual(
                    ast.unparse(assignment.value), f"parse_usernames({argument})"
                )
        direct = ast.parse((_ROOT / "cli" / "commands" / "direct.py").read_text())
        analyze = next(
            node
            for node in direct.body
            if isinstance(node, ast.FunctionDef) and node.name == "cmd_analyze"
        )
        self.assertEqual(
            ast.unparse(analyze.body[1]), "excluded_users = parse_usernames(excluded)"
        )


if __name__ == "__main__":
    unittest.main()
