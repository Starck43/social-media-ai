"""Apply the existing offline CI rules to the P21-T11 delta against a git base.

Syntax, leaf imports and migration graph cover the whole checkout. Formatting
and secret scans cover changed files against the unchanged reviewed baselines.
This delta check does not certify or rewrite inherited repository-wide debt.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from scripts.ci import checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    args = parser.parse_args()
    paths = checks.tracked_files()
    changed = [
        Path(name)
        for name in subprocess.check_output(["git", "diff", "--name-only", "--diff-filter=ACMR", f"{args.base}...HEAD"])
        .decode()
        .splitlines()
    ]
    for path in changed:
        if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", str(path)]).returncode:
            raise RuntimeError("Commit the checked delta before recording its evidence")
    checks.syntax([path for path in paths if path.suffix == ".py"])
    manifest = json.loads((checks.ROOT / ".ci/offline-tests.json").read_text())
    checks.pure_imports([checks.ROOT / path for path in manifest["source_modules"]])
    _, heads = checks.migration_graph(checks.ROOT / "migrations/versions")
    baseline = {
        "formatting": json.loads((checks.ROOT / ".ci/format-baseline.json").read_text()),
        "secrets": json.loads((checks.ROOT / ".ci/secret-baseline.json").read_text()),
    }
    actual = {
        "formatting": checks.formatting([path for path in changed if path.suffix == ".py"], baseline["formatting"]),
        "secrets": checks.secret_findings(changed),
    }
    failures = checks.compare_baseline(actual, baseline)
    for failure in failures:
        print(failure)
    if failures:
        return 1
    print(f"PASS: whole-tree syntax/leaf boundaries/single migration head {heads}; delta formatting and secrets")
    print("Inherited repository-wide formatting/secret-baseline diagnostics are not waived or changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
