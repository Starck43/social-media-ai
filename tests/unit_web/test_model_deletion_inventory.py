"""All declarations + real ORM metadata, isolated subprocess, no database or app startup."""

import ast
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def inventory():
    result = subprocess.run(
        [sys.executable, str(ROOT / "tests/model_deletion_inventory.py")], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("path", sorted((ROOT / "app/models").rglob("*.py")), ids=lambda p: str(p.relative_to(ROOT)))
def test_every_model_manager_and_compatibility_source_parses(path):
    ast.parse(path.read_text(), filename=str(path))


def test_all_canonical_tables_and_models_configure_and_compile_postgres(inventory):
    assert len(inventory["models"]) == 45
    assert len(inventory["tables"]) == 48
    assert len(set(inventory["models"])) == 45
    for table in inventory["tables"]:
        assert f'offline_audit.{table["table"]}' in table["ddl"]
        for fk in table["foreign_keys"]:
            assert fk["target_table"] in {t["table"] for t in inventory["tables"]}
            assert len(fk["columns"]) == len(fk["target_columns"]) == len(fk["nullable"])
            if fk["ondelete"] == "SET NULL":
                assert all(fk["nullable"]), (table["table"], fk)
            if fk["ondelete"] != "NO ACTION":
                assert "ON DELETE " + fk["ondelete"] in table["ddl"]
    assert Counter(fk["ondelete"] for t in inventory["tables"] for fk in t["foreign_keys"]) == {
        "CASCADE": 34,
        "SET NULL": 16,
        "RESTRICT": 46,
        "NO ACTION": 4,
    }


@pytest.mark.parametrize("model", ["Platform", "Source", "LLMProvider", "ModelType"])
def test_loaded_child_traversal_uses_real_orm_cascade(model, inventory):
    row = next(r for r in inventory["cascades"] if r["model"] == model)
    assert row["reaches_child"]


def test_known_passive_delete_mismatch_is_reported_not_silently_migrated(inventory):
    rel = next(r for r in inventory["relationships"] if r["model"] == "ModelType" and r["name"] == "permissions")
    assert rel["passive_deletes"] and "delete" in rel["cascade"]
    permission = next(t for t in inventory["tables"] if t["table"] == "permissions")
    assert permission["foreign_keys"][0]["ondelete"] == "NO ACTION"


def test_set_null_and_billing_restrict_preserve_history(inventory):
    tables = {t["table"]: t for t in inventory["tables"]}
    for table, column in [
        ("jobs", "agent_task_id"),
        ("ai_analytics", "parent_analysis_id"),
        ("agent_feedback", "message_id"),
    ]:
        fk = next(f for f in tables[table]["foreign_keys"] if column in f["columns"])
        assert fk["ondelete"] == "SET NULL"
    for name, table in tables.items():
        if name.startswith("personal_") or name == "task_billing_payers":
            assert all(f["ondelete"] == "RESTRICT" for f in table["foreign_keys"]), name
