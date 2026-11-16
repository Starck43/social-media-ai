"""Run focused pytest coverage separately for each explicit source target.

No DB isolation is implied: select the test execution route deliberately.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "scripts/ci/coverage_packages.toml"


def selected_from_manifest(ref: str) -> list[tuple[str, list[str]]]:
    config = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    changes = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMR", ref, "--"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    selected = []
    for entry in config.get("package", []):
        path = entry["path"].rstrip("/")
        if any(name == path or name.startswith(path + "/") for name in changes):
            selected.append((path, entry["tests"]))
    return selected


def measure(target: str, tests: list[str], minimum: float, no_conftest: bool) -> tuple[bool, float | None]:
    source = (ROOT / target).resolve()
    if not source.is_relative_to(ROOT) or not source.exists() or (not source.is_dir() and source.suffix != ".py"):
        print(f"ERROR: source target is missing or invalid: {target}", file=sys.stderr)
        return False, None
    if not tests:
        print(f"ERROR: no focused tests specified for {target}", file=sys.stderr)
        return False, None
    coverage_source = source if source.is_dir() else source.parent
    with tempfile.TemporaryDirectory(prefix="p13-coverage-") as temp:
        report = Path(temp) / "coverage.json"
        junit = Path(temp) / "pytest.xml"
        env = os.environ.copy()
        env["COVERAGE_FILE"] = str(Path(temp) / ".coverage")
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            *tests,
            f"--cov={coverage_source}",
            f"--cov-report=json:{report}",
            f"--junitxml={junit}",
            "--cov-report=",
        ]
        if no_conftest:
            cmd += ["--noconftest", "-p", "no:cacheprovider", "-o", "addopts="]
        print(f"\nTesting {target}: {' '.join(cmd)}", flush=True)
        result = subprocess.run(cmd, cwd=ROOT, env=env, check=False)
        if result.returncode or not report.is_file():
            print(f"ERROR: pytest failed or produced no coverage for {target} (exit {result.returncode})")
            return False, None
        if not junit.is_file():
            print(f"ERROR: pytest produced no test count for {target}", file=sys.stderr)
            return False, None
        root = ET.parse(junit).getroot()
        suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
        executed = sum(int(suite.attrib.get("tests", "0")) - int(suite.attrib.get("skipped", "0")) for suite in suites)
        if executed <= 0:
            print(f"ERROR: no tests executed for {target}", file=sys.stderr)
            return False, None
        data = json.loads(report.read_text(encoding="utf-8"))
        files = []
        for name, record in data["files"].items():
            filename = (ROOT / name).resolve()
            if filename == source or (source.is_dir() and filename.is_relative_to(source)):
                files.append(record["summary"])
        total = sum(item["num_statements"] for item in files)
        covered = sum(item["covered_lines"] for item in files)
        if not files or not total:
            print(f"ERROR: no measurable statements for {target}", file=sys.stderr)
            return False, None
        percent = 100.0 * covered / total
        return percent >= minimum, percent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="*", help="Existing source files or package directories, relative to the repo")
    parser.add_argument("--min", type=float, default=80.0, dest="minimum")
    parser.add_argument("--tests", nargs="+", help="Explicit focused pytest paths for each requested target")
    parser.add_argument("--changed-from", metavar="GIT_REF", help="Use manifest entries touched since this ref")
    parser.add_argument("--no-conftest", action="store_true", help="For proven DB-exempt tests only")
    args = parser.parse_args(argv)
    if not 0 <= args.minimum <= 100:
        parser.error("--min must be between 0 and 100")
    if bool(args.changed_from) == bool(args.targets):
        parser.error("provide source targets OR --changed-from, not both")
    if args.changed_from and args.tests:
        parser.error("--changed-from uses the tests in the manifest")
    if args.targets and not args.tests:
        parser.error("explicit targets require --tests")
    try:
        entries = (
            selected_from_manifest(args.changed_from) if args.changed_from else [(t, args.tests) for t in args.targets]
        )
        if not entries:
            print("No registered package changes; no coverage measured.")
            return 0
        results = [(target, *measure(target, tests, args.minimum, args.no_conftest)) for target, tests in entries]
    except (OSError, subprocess.CalledProcessError, KeyError, ValueError, ET.ParseError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print("\nTarget | Coverage | Result")
    for target, ok, percent in results:
        print(
            f"{target} | {percent:.2f}% | {'PASS' if ok else 'FAIL'}"
            if percent is not None
            else f"{target} | n/a | FAIL"
        )
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
