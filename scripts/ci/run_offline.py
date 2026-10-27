"""Run only the reviewed manifest, with no inherited plugins or DB fixtures."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    manifest = json.loads((ROOT / ".ci/offline-tests.json").read_text())
    env = os.environ.copy()
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTEST_ADDOPTS="", PYTEST_PLUGINS="", PYTHONPATH=str(ROOT))
    command = [
        sys.executable,
        "-m",
        "pytest",
        "--rootdir",
        str(ROOT),
        "-c",
        ".ci/pytest.ini",
        "-p",
        "scripts.ci.offline_guard",
        *manifest["tests"],
        "-q",
    ]
    return subprocess.call(command, cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
