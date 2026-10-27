"""Pytest plugin installed before collection, not a DB-dependent conftest."""

import importlib.abc
import socket
import sys

import pytest

BLOCKED = {"app", "cli", "sqlalchemy", "alembic", "asyncpg", "psycopg", "psycopg2", "requests", "httpx", "telethon"}


class OfflineImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if (
            fullname.split(".")[0] in BLOCKED
            or fullname.startswith("tests.conftest")
            or fullname == "scripts.setup_test_db"
        ):
            raise ImportError(f"Offline lane forbids runtime import: {fullname}")
        return None


def denied(*args, **kwargs):
    raise RuntimeError("Offline lane forbids network access")


def pytest_configure(config):
    loaded = sorted(name for name in sys.modules if name.split(".")[0] in BLOCKED)
    if loaded:
        raise pytest.UsageError(f"Offline lane was contaminated before collection: {loaded}")
    if not config.getoption("noconftest"):
        raise pytest.UsageError("Offline lane requires --noconftest; root fixtures access DB settings")
    config._offline_import_guard = OfflineImports()
    sys.meta_path.insert(0, config._offline_import_guard)
    config._offline_network_patch = pytest.MonkeyPatch()
    for target, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"), (socket, "getaddrinfo")):
        config._offline_network_patch.setattr(target, name, denied)


def pytest_unconfigure(config):
    if hasattr(config, "_offline_network_patch"):
        config._offline_network_patch.undo()
    guard = getattr(config, "_offline_import_guard", None)
    if guard in sys.meta_path:
        sys.meta_path.remove(guard)


def pytest_sessionfinish(session, exitstatus):
    # A skip/xfail must not turn a missing boundary into a green safety net.
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter and any(reporter.stats.get(key) for key in ("skipped", "xfailed", "xpassed")):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
