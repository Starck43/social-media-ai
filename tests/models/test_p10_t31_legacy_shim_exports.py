"""Historical migration-header guard; model-shim checks live in P11-T26."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_0117_header_matches_unchanged_real_parent():
    tree = ast.parse((ROOT / "scripts/migrations/legacy_definitions/0117_capability_entitlements.py").read_text())
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"revision", "down_revision"}
    }
    assert assignments == {"revision": "0117", "down_revision": "0116"}
    assert "Revision ID: 0117" in ast.get_docstring(tree)
    assert "Revises: 0116" in ast.get_docstring(tree)
