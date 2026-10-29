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


def test_alembic_history_is_one_initial_revision():
    revisions = sorted(VERSIONS.glob("*.py"))
    assert len(revisions) == 1, f"Expected one executable revision, found {[p.name for p in revisions]}"

    metadata = _metadata(revisions[0])
    assert isinstance(metadata.get("revision"), str) and metadata["revision"]
    assert metadata.get("down_revision") is None


def test_initial_schema_resource_contains_sql_without_psql_commands():
    sql = (VERSIONS / "baseline_0118.sql").read_text(encoding="utf-8")
    assert not re.search(r"(?m)^\s*\\", sql), "psql client commands cannot execute through Alembic"
