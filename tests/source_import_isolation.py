"""Load actual source with module-local import doubles, not global replacements.

Only explicit declared dependencies are redirected; whole-module imports need
separate module_imports opt-in (never global sys.modules replacements). Unmapped
imports keep Python's normal behavior. The private source alias is registered
only during execution (dataclasses need it), then its previous binding is restored.
"""

import builtins
import importlib.util
import sys
from pathlib import Path

_MISSING = object()


def load_isolated_source(name, path, imports=None, *, module_imports=None):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load isolated source: {path}")
    module = importlib.util.module_from_spec(spec)
    bindings = dict(imports or {})
    module_bindings = dict(module_imports or {})
    module.__isolated_imports__ = bindings
    normal_import = builtins.__import__

    def isolated_import(import_name, globals=None, locals=None, fromlist=(), level=0):
        absolute = import_name
        if level:
            package = (globals or {}).get("__package__")
            absolute = importlib.util.resolve_name("." * level + import_name, package)
        if not fromlist and absolute in module_bindings:
            return module_bindings[absolute]
        if absolute in bindings:
            if not fromlist:
                raise ImportError("Declared test doubles require an explicit from-import")
            return bindings[absolute]
        return normal_import(import_name, globals, locals, fromlist, level)

    module.__builtins__ = dict(vars(builtins), __import__=isolated_import)
    previous = sys.modules.get(name, _MISSING)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is _MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
    return module
