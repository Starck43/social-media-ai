"""Run the required OneC package gate plus P17-T05 DB/permission checks.

Use prepare_p17_t05_validation.py's locked run action with a prepared isolated
schema. Root conftest validates and redirects the canonical working target.
"""

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    manifest = tomllib.loads((ROOT / "scripts/ci/coverage_packages.toml").read_text())
    tests = list(next(entry["tests"] for entry in manifest["package"] if entry["path"] == "app/services/onec"))
    tests += sorted(str(path.relative_to(ROOT)) for path in (ROOT / "tests/integrations").glob("test_p17_t05_*.py"))
    tests += [
        "tests/security/test_web_permissions.py",
        "tests/security/test_permission_scope.py",
        "tests/security/test_access_policy.py",
    ]
    return subprocess.run(
        [sys.executable, "scripts/ci/package_coverage.py", "app/services/onec", "--min", "80", "--tests", *tests],
        cwd=ROOT,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
