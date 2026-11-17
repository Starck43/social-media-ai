"""Offline AST checks: every graph head needs an explicit rotation layout review."""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def rotation_layouts():
    tree = ast.parse((ROOT / "scripts/ops/rotate_credentials_key.py").read_text())
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id in {"GENERATIONS", "VERSIONED_GENERATIONS"}
            for target in node.targets
        )
    ]
    # Execute only the two reviewed constant expressions, never the operator module/imports.
    namespace = {"__builtins__": {}, "dict": dict, "frozenset": frozenset}
    exec(compile(ast.Module(body=assignments, type_ignores=[]), "rotation-layouts", "exec"), namespace)
    return namespace["GENERATIONS"], namespace["VERSIONED_GENERATIONS"]


def graph():
    revisions = {}
    for path in (ROOT / "migrations/versions").glob("*.py"):
        values = {}
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                        values[target.id] = ast.literal_eval(node.value)
        if "revision" in values:
            revisions[values["revision"]] = values.get("down_revision")
    return revisions


class RotationHeadTests(unittest.TestCase):
    def test_every_migration_head_has_a_reviewed_layout(self):
        revisions = graph()
        parents = {
            parent
            for value in revisions.values()
            for parent in (value if isinstance(value, (tuple, list)) else (value,))
            if parent is not None
        }
        heads = set(revisions) - parents
        self.assertTrue(heads)
        generations, versioned = rotation_layouts()
        self.assertFalse(heads - generations.keys(), "Review new encrypted stores before enabling rotation")
        # All current heads descend from credential-version introduction.
        for head in heads:
            current = head
            while current != "0153" and current is not None:
                current = revisions[current]
                self.assertNotIsInstance(current, (tuple, list), "Review merged schema generations explicitly")
            if current == "0153":
                self.assertIn(head, versioned)

    def test_reviewed_successors_keep_versioned_encrypted_stores(self):
        generations, versioned = rotation_layouts()
        for revision in (
            "0153",
            "0154",
            "0155",
            "0156",
            "p21t13_catalog_storage",
            "p21t14_rate_limits",
            "0157",
            "p12t15_policy_audit",
        ):
            with self.subTest(revision=revision):
                self.assertEqual(generations[revision], (True, True, True))
                self.assertIn(revision, versioned)
        self.assertNotIn("unreviewed_future", generations)
        self.assertNotIn("0128", versioned)


if __name__ == "__main__":
    unittest.main()
