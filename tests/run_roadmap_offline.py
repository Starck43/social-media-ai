"""Run one inspected direct-unittest file, rejecting undeclared app imports.

Usage: python tests/run_roadmap_offline.py tests/test_roadmap_reconciliation.py
Explicit module-local doubles and already installed sys.modules fixtures still
work. This guard is not a general sandbox and does not make DB tests safe.
Inspect every selected file/import graph first. Do not point it at conftest or
app-importing DB/ASGI tests; rejection is a blocker, never a skipped PASS.
"""

import importlib.abc
import runpy
import sys
from pathlib import Path


class NoApplicationImport(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "app" or fullname.startswith("app."):
            raise ImportError("Offline audit blocks undeclared application import: " + fullname)
        return None


if __name__ == "__main__":
    path = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(path.parent))
    sys.meta_path.insert(0, NoApplicationImport())
    sys.argv = [str(path), "-q"]
    runpy.run_path(str(path), run_name="__main__")
