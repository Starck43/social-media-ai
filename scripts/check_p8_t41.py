"""Guarded, source-only P8-T41 checks; no app install, DB or live VK needed.

Run: python -m scripts.check_p8_t41
Not the full suite and not integrated product acceptance. See docs/checks/p8-t41.md.
The guard is an accidental-access tripwire, not an OS security sandbox.
"""

from __future__ import annotations

import importlib.abc
import platform
import socket
import subprocess
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SUITES = (
    "tests.social.test_p8_t41_journey_plan",
    "tests.jobs.test_p8_t41_research_journey",
    "tests.tooling.test_p8_t41_surface_contracts",
    "tests.tooling.test_p8_t41_runner",
    "tests.social.test_p8_t38_vk_access",
)
BLOCKED = frozenset(
    {"app", "cli", "sqlalchemy", "alembic", "asyncpg", "psycopg", "psycopg2", "httpx", "requests", "telethon"}
)


class SourceOnlyImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if (
            fullname.split(".")[0] in BLOCKED
            or fullname.startswith("tests.conftest")
            or fullname == "scripts.setup_test_db"
        ):
            raise ImportError(f"P8-T41 source-only route forbids runtime import: {fullname}")
        return None


def deny_network(*_args, **_kwargs):
    raise RuntimeError("P8-T41 source-only route forbids network access")


@contextmanager
def source_only_guard():
    loaded = sorted(name for name in sys.modules if name.split(".")[0] in BLOCKED)
    if loaded:
        raise RuntimeError(f"P8-T41 source-only process already has runtime imports: {loaded}")
    finder = SourceOnlyImports()
    sys.meta_path.insert(0, finder)
    try:
        with (
            patch.object(socket.socket, "connect", deny_network),
            patch.object(socket.socket, "connect_ex", deny_network),
            patch.object(socket, "getaddrinfo", deny_network),
            patch.object(socket, "create_connection", deny_network),
        ):
            yield
    finally:
        sys.meta_path.remove(finder)


def main():
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    print(f"P8-T41 source-only | SHA={revision} | dirty={dirty} | Python={platform.python_version()}", flush=True)
    with source_only_guard():
        suite = unittest.defaultTestLoader.loadTestsFromNames(SUITES)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    # A skip is missing evidence, not a passing lane.
    return 0 if result.wasSuccessful() and not result.skipped and result.testsRun > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
