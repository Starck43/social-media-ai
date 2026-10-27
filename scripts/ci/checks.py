"""Offline static checks. Migration files are parsed, never imported/executed."""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / ".ci/baseline.json"


def tracked_files(root=ROOT):
    return [
        Path(name) for name in subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0") if name
    ]


def read_python(path):
    with tokenize.open(path) as stream:
        return stream.read()


def syntax(paths):
    for path in paths:
        compile(read_python(path), str(path), "exec", dont_inherit=True)


def pure_imports(paths):
    """Keep reviewed file-loaded leaf modules stdlib-only (including local imports)."""
    allowed = sys.stdlib_module_names | {"__future__"}
    for path in paths:
        for node in ast.walk(ast.parse(read_python(path))):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
                if node.level:
                    raise ValueError(f"Relative import crosses offline boundary: {path}:{node.lineno}")
            else:
                continue
            if set(names) - allowed:
                raise ValueError(f"Non-stdlib import crosses offline boundary: {path}:{node.lineno}: {names}")


def migration_graph(directory):
    """Read literal Alembic metadata with AST, without env.py/settings/Alembic."""
    revisions = {}
    for path in sorted(directory.glob("*.py")):
        metadata = {}
        for node in ast.parse(read_python(path)).body:
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target] if isinstance(node, ast.AnnAssign) else []
            )
            for target in targets:
                if isinstance(target, ast.Name) and target.id in {"revision", "down_revision", "depends_on"}:
                    if target.id in metadata:
                        raise ValueError(f"Duplicate migration metadata: {path}:{target.id}")
                    metadata[target.id] = ast.literal_eval(node.value)
        revision = metadata.get("revision")
        if not isinstance(revision, str) or not revision or "down_revision" not in metadata:
            raise ValueError(f"Missing/literal-invalid migration metadata: {path}")
        if revision in revisions:
            raise ValueError(f"Duplicate migration revision: {revision}")
        parents = []
        for key in ("down_revision", "depends_on"):
            value = metadata.get(key)
            values = value if isinstance(value, (tuple, list)) else [] if value is None else [value]
            if not all(isinstance(parent, str) and parent for parent in values):
                raise ValueError(f"Invalid migration parent: {path}:{key}")
            parents.extend(values)
        revisions[revision] = parents
    if not revisions:
        raise ValueError("Empty migration graph")
    visited, active = set(), set()

    def visit(revision):
        if revision not in revisions:
            raise ValueError(f"Unknown migration parent: {revision}")
        if revision in active:
            raise ValueError(f"Migration cycle: {revision}")
        if revision not in visited:
            active.add(revision)
            for parent in revisions[revision]:
                visit(parent)
            active.remove(revision)
            visited.add(revision)

    for revision in revisions:
        visit(revision)
    heads = set(revisions) - {parent for parents in revisions.values() for parent in parents}
    if len(heads) != 1:
        raise ValueError(f"Expected one migration head, found: {sorted(heads)}")
    return len(revisions), sorted(heads)


def formatting(paths, baseline=None):
    import black
    import isort

    mode = black.parse_pyproject_toml(str(ROOT / "pyproject.toml"))
    black_mode = black.FileMode(
        line_length=int(mode["line_length"]),
        target_versions={black.TargetVersion[value.upper()] for value in mode["target_version"]},
    )
    isort_config = isort.Config(settings_file=str(ROOT / "pyproject.toml"))
    violations = {}
    for path in paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        prior = (baseline or {}).get(path.as_posix())
        # Frozen exceptions are valid only for their exact original contents.
        if prior and prior["sha256"] == digest:
            violations[path.as_posix()] = prior
            continue
        source = read_python(path)
        tools = []
        if black.format_str(source, mode=black_mode) != source:
            tools.append("black")
        if isort.code(source, config=isort_config) != source:
            tools.append("isort")
        if tools:
            violations[path.as_posix()] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "tools": tools}
    return violations


def secret_findings(paths):
    from detect_secrets import SecretsCollection
    from detect_secrets.settings import default_settings

    secrets = SecretsCollection()
    with default_settings() as settings:
        # Verification may perform external requests; never enable it in this lane.
        settings.disable_filters("detect_secrets.filters.common.is_ignored_due_to_verification_policies")
        for path in paths:
            if path.is_file() and path not in {
                Path(".ci/baseline.json"),
                Path(".ci/format-baseline.json"),
                Path(".ci/secret-baseline.json"),
            }:
                secrets.scan_file(str(path))
    # Keep only hashes/types/paths, not matched text, context or line numbers.
    return sorted(
        f"{path}:{secret.type}:{secret.secret_hash}" for path, findings in secrets.data.items() for secret in findings
    )


def compare_baseline(actual, baseline):
    failures = []
    for path, violation in actual["formatting"].items():
        if baseline["formatting"].get(path) != violation:
            failures.append(f"Format changed/new file with Black/isort 120: {path}")
    for finding in set(actual["secrets"]) - set(baseline["secrets"]):
        failures.append(f"New secret candidate (hash only): {finding}")
    return failures


def main():
    paths = tracked_files()
    python = [path for path in paths if path.suffix == ".py"]
    syntax(python)
    manifest = json.loads((ROOT / ".ci/offline-tests.json").read_text())
    pure_imports([ROOT / path for path in manifest["source_modules"]])
    count, heads = migration_graph(ROOT / "migrations/versions")
    baseline = json.loads(BASELINE.read_text())
    baseline["formatting"] = json.loads((ROOT / ".ci/format-baseline.json").read_text())
    baseline["secrets"] = json.loads((ROOT / ".ci/secret-baseline.json").read_text())
    actual = {"formatting": formatting(python, baseline["formatting"]), "secrets": secret_findings(paths)}
    failures = compare_baseline(actual, baseline)
    print(f"Syntax: {len(python)} files; stdlib leaf boundaries: {len(manifest['source_modules'])}")
    print(f"AST migration graph: {count} revisions, heads={heads}; no migration execution")
    print(
        f"Baseline inventory: {len(actual['formatting'])} formatting files, {len(actual['secrets'])} secret candidates"
    )
    for failure in failures:
        print(failure)
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
