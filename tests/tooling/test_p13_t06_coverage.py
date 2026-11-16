"""No-DB acceptance tests execute the actual CLI against disposable packages."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "scripts/ci/package_coverage.py"


@pytest.fixture
def sample_repo(tmp_path):
    script = tmp_path / "scripts/ci/package_coverage.py"
    script.parent.mkdir(parents=True)
    shutil.copyfile(SOURCE, script)
    (script.parent / "coverage_packages.toml").write_text("")
    for name in ("covered", "uncovered"):
        package = tmp_path / "app" / name
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
    (tmp_path / "app/covered/logic.py").write_text("def value():\n    return 7\n")
    (tmp_path / "app/uncovered/logic.py").write_text("def one():\n    return 1\n\ndef two():\n    return 2\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_sample.py").write_text(
        "from app.covered.logic import value\nfrom app.uncovered.logic import one\n"
        "def test_values():\n    assert value() == 7\n    assert one() == 1\n"
    )
    return tmp_path, script


def run_gate(sample_repo, *args):
    root, script = sample_repo
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    env.pop("PYTEST_ADDOPTS", None)
    return subprocess.run([sys.executable, str(script), *args], cwd=root, env=env, text=True, capture_output=True)


def test_gate_passes_above_threshold(sample_repo):
    result = run_gate(sample_repo, "app/covered", "--min", "80", "--tests", "tests/test_sample.py", "--no-conftest")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "app/covered | 100.00% | PASS" in result.stdout


def test_per_target_gate_rejects_low_coverage_even_if_other_target_passes(sample_repo):
    result = run_gate(
        sample_repo, "app/covered", "app/uncovered", "--min", "80", "--tests", "tests/test_sample.py", "--no-conftest"
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "app/covered | 100.00% | PASS" in result.stdout
    assert "app/uncovered | 75.00% | FAIL" in result.stdout


def test_single_file_and_no_statements_fail_closed(sample_repo):
    good = run_gate(
        sample_repo, "app/covered/logic.py", "--min", "80", "--tests", "tests/test_sample.py", "--no-conftest"
    )
    assert good.returncode == 0, good.stdout + good.stderr
    empty = run_gate(
        sample_repo, "app/covered/__init__.py", "--min", "80", "--tests", "tests/test_sample.py", "--no-conftest"
    )
    assert empty.returncode == 1
    assert "no measurable statements" in empty.stderr


def test_missing_target_failed_tests_and_invalid_arguments_fail(sample_repo):
    absent = run_gate(sample_repo, "app/missing", "--tests", "tests/test_sample.py", "--no-conftest")
    assert absent.returncode == 1
    missing_test = run_gate(sample_repo, "app/covered", "--tests", "tests/missing.py", "--no-conftest")
    assert missing_test.returncode == 1
    no_tests = run_gate(sample_repo, "app/covered")
    assert no_tests.returncode == 2
    invalid_min = run_gate(sample_repo, "app/covered", "--min", "101", "--tests", "tests/test_sample.py")
    assert invalid_min.returncode == 2


def test_manifest_selection_checks_only_touched_package(sample_repo):
    root, script = sample_repo
    (script.parent / "coverage_packages.toml").write_text(
        '[[package]]\npath="app/covered"\ntests=["tests/test_sample.py"]\n'
        '[[package]]\npath="app/uncovered"\ntests=["tests/test_sample.py"]\n'
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "baseline"],
        cwd=root,
        check=True,
    )
    (root / "app/covered/logic.py").write_text("def value():\n    return 7\n# touched\n")
    result = run_gate(sample_repo, "--changed-from", "HEAD", "--no-conftest")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "app/covered | 100.00% | PASS" in result.stdout
    assert "app/uncovered |" not in result.stdout


def test_unimported_source_is_included_and_all_skipped_tests_fail(sample_repo):
    root, _ = sample_repo
    (root / "app/covered/unseen.py").write_text("def unseen():\n    return 5\n")
    low = run_gate(sample_repo, "app/covered", "--min", "80", "--tests", "tests/test_sample.py", "--no-conftest")
    assert low.returncode == 1, low.stdout + low.stderr
    (root / "tests/test_skipped.py").write_text(
        "import pytest\nfrom app.covered.logic import value\n@pytest.mark.skip(reason='synthetic')\n"
        "def test_never():\n    assert value() == 7\n"
    )
    skipped = run_gate(
        sample_repo, "app/covered/logic.py", "--min", "0", "--tests", "tests/test_skipped.py", "--no-conftest"
    )
    assert skipped.returncode == 1, skipped.stdout + skipped.stderr
    assert "no tests executed" in skipped.stderr
