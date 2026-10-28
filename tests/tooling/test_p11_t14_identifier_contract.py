"""Stdlib/source-only helper contracts; no app bootstrap or persisted state."""

from __future__ import annotations

import ast
import importlib.abc
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("_p11_t14_loader", ROOT / "tests/source_import_isolation.py")
LOADER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LOADER)


def load(name, path, imports=None):
    return LOADER.load_isolated_source(name, ROOT / path, imports)


def function(path, name, **namespace):
    """Execute only a parsed pure function, never the surrounding app module."""
    node = next(
        n for n in ast.parse((ROOT / path).read_text()).body if isinstance(n, ast.FunctionDef) and n.name == name
    )
    scope = {"Any": object, **namespace}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), scope)
    return scope[name]


class IntSubclass(int):
    pass


class NoCoercion:
    def __int__(self):
        raise AssertionError("Do not coerce IDs")

    def __gt__(self, other):
        raise AssertionError("Do not compare non-IDs")


class IdentifierContractTests(unittest.TestCase):
    def setUp(self):
        self.before = {n for n in sys.modules if n == "app" or n.startswith("app.")}
        self.ids = load("_p11_t14_identifiers", "app/utils/identifiers.py")

    def tearDown(self):
        self.assertEqual({n for n in sys.modules if n == "app" or n.startswith("app.")}, self.before)

    def test_strict_positive_int_edges_and_no_coercion(self):
        for value, expected in (
            (None, False),
            (False, False),
            (True, False),
            (0, False),
            (-1, False),
            ("1", False),
            (1.0, False),
            (IntSubclass(1), False),
            (NoCoercion(), False),
            (1, True),
            (2147483647, True),
            (2147483648, True),
            (2**100, True),
        ):
            with self.subTest(value=type(value).__name__, expected=expected):
                self.assertIs(self.ids.strict_positive_int(value), expected)

    def test_installed_wheel_guard_reviews_only_exact_pure_helper(self):
        outer = ast.parse((ROOT / "tests/wheel_runtime_smoke.py").read_text())
        source = next(
            ast.literal_eval(node.value)
            for node in outer.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "INSTALLED_CHECK" for target in node.targets)
        )
        tree = ast.parse(source)
        allowed = next(
            ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "allowed" for target in node.targets)
        )
        self.assertIn("app.utils.identifiers", allowed)
        node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "SafeImports")
        scope = {"importlib": importlib, "allowed": allowed}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<wheel import guard>", "exec"), scope)
        finder = scope["SafeImports"]()
        self.assertIsNone(finder.find_spec("app.utils.identifiers", None))
        for name in (
            "app.utils.unreviewed",
            "app.utils.identifiers.adapter",
            "app.models",
            "app.web",
            "app.agent",
            "cli.unreviewed",
        ):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "Unreviewed application import"):
                finder.find_spec(name, None)

    def test_selected_consumers_reuse_same_predicate_and_keep_errors(self):
        imports = {"app.utils.identifiers": self.ids}
        policy = load("_p11_t14_vk_policy", "app/services/social/vk_access.py", imports)
        ledger = load("_p11_t14_checkpoint", "app/services/digest/checkpoints.py", imports)
        self.assertIs(policy._positive_id, self.ids.strict_positive_int)
        self.assertIs(ledger._positive_id, self.ids.strict_positive_int)
        for invalid in (True, False, None, 0, -1, "1", 1.0, IntSubclass(1)):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(policy.VKSelectionError, "^vk_access_credential_required$"):
                    policy.VKSelection("user", invalid)
                with self.assertRaisesRegex(ledger.CheckpointError, "^Invalid or mismatched digest checkpoint$"):
                    ledger.new_checkpoint(
                        run_id=invalid,
                        tenant_id=2,
                        generation="g",
                        content="report",
                        splitter="v1",
                        targets=[{"binding_id": 1, "channel": "telegram", "destination_id": "-100", "parts": ["part"]}],
                    )
        self.assertEqual(policy.VKSelection("user", 2**100).credential_id, 2**100)

    def test_different_id_contracts_remain_different(self):
        bounded = function("app/services/ai/dispatch_receipts.py", "_positive_id")
        subclass_aware = function("app/agent/identity.py", "_positive_id")
        self.assertFalse(bounded(2147483648))
        self.assertTrue(self.ids.strict_positive_int(2147483648))
        self.assertTrue(subclass_aware(IntSubclass(1)))
        self.assertFalse(self.ids.strict_positive_int(IntSubclass(1)))
        for predicate in (bounded, subclass_aware):
            for invalid in (True, False, None, "1", 0, -1, 1.0):
                self.assertIs(predicate(invalid), False)

    def test_truncation_defaults_and_falsy_coercion_remain_domain_owned(self):
        for path, default in (
            ("app/services/collection/mention_lanes.py", 160),
            ("app/services/collection/population.py", 160),
            ("app/services/collection/lane_migration.py", 120),
        ):
            brief = function(path, "_brief", MAX_TEXT=160)
            self.assertEqual(len(brief("x" * 200)), default)
            self.assertEqual(brief(" a\n b  c ", 4), "a b ")
            for value in (None, False, 0, ""):
                self.assertEqual(brief(value), "")
            self.assertEqual(brief(True), "True")
            self.assertEqual(brief(" abc ", 0), "")
            self.assertEqual(brief(" abc ", -1), "ab")

    def test_existing_plural_delegation_retains_count_conversion(self):
        plural = function("app/web/deps.py", "plural")
        delegated = function("app/web/tasks.py", "_plural", plural=plural)
        for count, expected in (
            (0, "many"),
            (1, "one"),
            (2, "few"),
            (5, "many"),
            (11, "many"),
            (12, "many"),
            (14, "many"),
            (21, "one"),
            (22, "few"),
            (111, "many"),
            (-22, "few"),
            ("21", "one"),
            (True, "one"),
            (False, "many"),
            (2.9, "few"),
        ):
            self.assertEqual(plural(count, "one", "few", "many"), expected)
            self.assertEqual(delegated(count, "one", "few", "many"), expected)
        for invalid in (None, "invalid"):
            for predicate in (plural, delegated):
                with self.assertRaises((TypeError, ValueError)):
                    predicate(invalid, "one", "few", "many")


if __name__ == "__main__":
    unittest.main()
