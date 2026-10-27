"""Guarded collection ONLY for five explicitly reviewed synthetic-source suites.

This is not a generic pytest runner and does not authorize executing DB tests.
Requires the pinned dev profile. Run with -I; root conftest is deliberately absent
because real app imports, network, subprocesses and thread starts are forbidden.
"""

from __future__ import annotations

import argparse
import importlib.abc
import json
import os
import sys
import threading
from pathlib import Path

__test__ = False
PATHS = (
    "tests/unit_web/test_settings_layout.py",
    "tests/unit_web/test_settings_llm.py",
    "tests/unit_web/test_settings_platforms.py",
    "tests/unit_web/test_settings_review.py",
    "tests/unit_web/test_attention_feed.py",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, help="Optional sorted node-ID JSON outside the repository")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.output and args.output.resolve().is_relative_to(root):
        parser.error("Write discovery evidence outside the checkout")
    os.chdir(root)
    sys.path[:0] = [str(root), str(root / "tests")]
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    for key in list(os.environ):
        if any(term in key for term in ("SECRET", "TOKEN", "POSTGRES", "DATABASE", "PASSWORD", "API_KEY")):
            del os.environ[key]
    attempts = []

    def forbidden(reason):
        attempts.append(reason)
        raise AssertionError("Guarded discovery attempted " + reason)

    def audit(event, args):
        if event in {
            "socket.connect",
            "socket.bind",
            "socket.getaddrinfo",
            "socket.sendto",
            "subprocess.Popen",
            "os.system",
            "os.fork",
            "os.posix_spawn",
        }:
            forbidden(event)

    class NoRealApplicationImports(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "app" or fullname.startswith("app."):
                forbidden("real application import: " + fullname)
            return None

    sys.addaudithook(audit)
    sys.meta_path.insert(0, NoRealApplicationImports())
    threading.Thread.start = lambda *a, **kw: forbidden("thread startup")
    import pytest

    class Inventory:
        def pytest_collection_finish(self, session):
            nodes = sorted(item.nodeid for item in session.items)
            if args.output:
                args.output.resolve().write_text(json.dumps(nodes, indent=2) + "\n")
            print(f"Guarded navigation inventory: {len(nodes)} node IDs")

    result = pytest.main(
        [
            "--collect-only",
            "-q",
            "--noconftest",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
            "-o",
            "markers=asyncio: declared async test (collection only)",
            *PATHS,
        ],
        plugins=[Inventory()],
    )
    if attempts:
        raise SystemExit("Forbidden operation attempted during discovery: " + ", ".join(attempts))
    raise SystemExit(result)


if __name__ == "__main__":
    main()
