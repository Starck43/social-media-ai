"""Keep Alembic's executable history to one initial revision."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / "migrations/versions"


def _metadata(path: Path) -> dict[str, object]:
    result = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        targets = (
            node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        for target in targets:
            if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                result[target.id] = ast.literal_eval(node.value)
    return result


def test_alembic_history_has_one_base_and_one_head():
    metadata = {path.stem: _metadata(path) for path in sorted(VERSIONS.glob("*.py"))}
    assert metadata, "Expected an executable migration history"

    bases = [name for name, meta in metadata.items() if meta.get("down_revision") in (None, (), [])]
    assert len(bases) == 1, f"Expected one initial revision, found {bases}"

    parents: set[object] = set()
    for meta in metadata.values():
        parent = meta.get("down_revision")
        if isinstance(parent, tuple):
            parents.update(parent)
        elif parent:
            parents.add(parent)
    heads = sorted(meta.get("revision") for meta in metadata.values() if meta.get("revision") not in parents)
    assert len(heads) == 1, f"Expected one head revision, found {heads}"


def test_initial_schema_resource_contains_sql_without_psql_commands():
    sql = (VERSIONS / "baseline_0118.sql").read_text(encoding="utf-8")
    assert not re.search(r"(?m)^\s*\\", sql), "psql client commands cannot execute through Alembic"


def test_check_comparison_accepts_only_equivalent_text_array_casts():
    from scripts.migrations.q14_compare_schemas import _norm_check

    outer = "CHECK (plan::text = ANY (ARRAY['starter'::character varying,'pro'::character varying]::text[]))"
    inner = "CHECK (plan::text = ANY (ARRAY['starter'::character varying::text,'pro'::character varying::text]))"
    assert _norm_check(outer, "public") == _norm_check(inner, "test_schema")
    assert _norm_check(outer, "public") != _norm_check(inner.replace("'pro'", "'business'"), "test_schema")
    assert _norm_check(outer.replace(" = ANY", " <> ANY"), "public") != _norm_check(inner, "test_schema")
    bounded = outer.replace("character varying", "character varying(3)")
    assert _norm_check(bounded, "public") != _norm_check(inner, "test_schema")
    overloaded = "CHECK (custom(ARRAY['starter'::character varying]::text[]))"
    assert _norm_check(overloaded, "public") == overloaded
