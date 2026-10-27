"""Regression checks for the offline safety net; no project fixtures or app imports."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("offline_checks", ROOT / "scripts/ci/checks.py")
CHECKS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKS)


def revision(directory, name, parent, extra=""):
    (directory / f"{name}.py").write_text(f"revision = {name!r}\ndown_revision = {parent!r}\n{extra}")


def test_graph_never_executes_migration_top_level(tmp_path):
    revision(tmp_path, "a", None, "raise RuntimeError('must not execute')")
    revision(tmp_path, "b", "a", "import app.core.config")
    assert CHECKS.migration_graph(tmp_path) == (2, ["b"])


@pytest.mark.parametrize("kind", ["missing", "duplicate", "cycle", "heads", "dynamic", "empty", "dependency"])
def test_graph_fails_closed(tmp_path, kind):
    if kind != "empty":
        revision(tmp_path, "a", "b" if kind == "cycle" else None)
    if kind == "missing":
        revision(tmp_path, "b", "absent")
    elif kind == "duplicate":
        (tmp_path / "other.py").write_text("revision='a'\ndown_revision=None")
    elif kind == "cycle":
        revision(tmp_path, "b", "a")
    elif kind == "heads":
        revision(tmp_path, "b", None)
    elif kind == "dynamic":
        (tmp_path / "other.py").write_text("revision=get_revision()\ndown_revision=None")
    elif kind == "dependency":
        revision(tmp_path, "b", "a", "depends_on='missing'")
    with pytest.raises((ValueError, TypeError)):
        CHECKS.migration_graph(tmp_path)


def test_baseline_cannot_grow_or_cover_changed_formatting():
    baseline = {"formatting": {"old.py": {"sha256": "before", "tools": ["black"]}}, "secrets": ["known-hash"]}
    assert not CHECKS.compare_baseline(baseline, baseline)
    assert not CHECKS.compare_baseline({"formatting": {}, "secrets": []}, baseline)
    actual = {"formatting": {"old.py": {"sha256": "after", "tools": ["black"]}}, "secrets": ["new-hash"]}
    assert len(CHECKS.compare_baseline(actual, baseline)) == 2


def test_leaf_boundary_checks_imports_inside_functions(tmp_path):
    path = tmp_path / "leaf.py"
    path.write_text("def late():\n    import app.core.database\n")
    with pytest.raises(ValueError, match="boundary"):
        CHECKS.pure_imports([path])


@pytest.mark.parametrize(
    "statement",
    [
        "import app.core.database",
        "import sqlalchemy",
        "import tests.conftest",
        "import socket; socket.create_connection(('localhost', 5432))",
    ],
)
def test_guard_rejects_runtime_db_fixtures_and_network_before_test_body(tmp_path, statement):
    test = tmp_path / "test_probe.py"
    test.write_text(f"{statement}\ndef test_probe(): pass\n")
    env = os.environ.copy()
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTEST_ADDOPTS="", PYTEST_PLUGINS="", PYTHONPATH=str(ROOT))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(ROOT / ".ci/pytest.ini"),
            "-p",
            "scripts.ci.offline_guard",
            str(test),
            "-q",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Offline lane forbids" in result.stdout + result.stderr


def test_no_database_packages_in_tool_profile():
    import importlib.metadata

    installed = {dist.metadata["Name"].lower() for dist in importlib.metadata.distributions()}
    assert not installed.intersection({"sqlalchemy", "asyncpg", "psycopg", "psycopg2-binary", "alembic"})


def test_manifest_is_exact_files_with_classification():
    manifest = json.loads((ROOT / ".ci/offline-tests.json").read_text())
    assert manifest["owner"] == "P11-T03"
    assert manifest["tests"]
    for path, reason in manifest["tests"].items():
        assert path.startswith("tests/") and path.endswith(".py") and (ROOT / path).is_file() and reason


def test_secret_scanner_is_offline_and_reports_only_fingerprints(tmp_path):
    # Scanner imports requests as a tool dependency, not as a provider client.
    # Keep it out of the stricter test collection process; trap its socket I/O.
    path = tmp_path / "candidate.txt"
    path.write_text("password = '" + "SyntheticOnly-" + "CI-Detector-Probe-123456789'\n")
    code = """
import json, socket, sys
from scripts.ci.checks import secret_findings
def denied(*args, **kwargs):
    raise AssertionError('scanner attempted network')
socket.socket.connect = socket.socket.connect_ex = socket.getaddrinfo = denied
from pathlib import Path
print(json.dumps(secret_findings([Path(sys.argv[1])])))
"""
    result = subprocess.run([sys.executable, "-c", code, str(path)], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    findings = json.loads(result.stdout)
    assert findings
    assert all("CI-Detector-Probe" not in finding for finding in findings)
    assert CHECKS.compare_baseline({"formatting": {}, "secrets": findings}, {"formatting": {}, "secrets": []})


def test_formatting_ratchet_detects_a_new_bad_file(tmp_path):
    path = tmp_path / "bad.py"
    path.write_text("import sys,os\nx= 1\n")
    violations = CHECKS.formatting([path])
    assert violations[path.as_posix()]["tools"] == ["black", "isort"]
    assert CHECKS.compare_baseline({"formatting": violations, "secrets": []}, {"formatting": {}, "secrets": []})


@pytest.mark.parametrize("outcome", ["skip", "xfail"])
def test_guard_does_not_accept_skipped_safety_checks(tmp_path, outcome):
    path = tmp_path / "test_outcome.py"
    path.write_text(f"import pytest\ndef test_outcome(): pytest.{outcome}('probe')\n")
    env = os.environ.copy()
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTEST_ADDOPTS="", PYTEST_PLUGINS="", PYTHONPATH=str(ROOT))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(ROOT / ".ci/pytest.ini"),
            "-p",
            "scripts.ci.offline_guard",
            str(path),
            "-q",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr


def test_workflow_is_read_only_tool_only_and_sha_pinned():
    import re

    import yaml

    workflow = yaml.load((ROOT / ".github/workflows/offline.yml").read_text(), Loader=yaml.BaseLoader)
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["offline"]
    assert "services" not in job and "env" not in job
    actions = [step["uses"] for step in job["steps"] if "uses" in step]
    assert len(actions) == 2 and all(re.fullmatch(r"actions/[a-z-]+@[0-9a-f]{40}", action) for action in actions)
    runs = [step["run"] for step in job["steps"] if "run" in step]
    assert runs == [
        "python -m pip install --require-hashes -r .ci/requirements.txt",
        "python -m scripts.ci.checks",
        "python -m scripts.ci.run_offline",
    ]
