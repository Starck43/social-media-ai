"""Scoped source-only import contract; never import the inspected application.

Review a new edge rather than widening an entire layer. Parent initializers,
relative, lazy, type-only and literal dynamic imports all count. This is a drift
tripwire for conventional Python imports, not an arbitrary-code security sandbox.
"""

from __future__ import annotations

import ast
import sys
from collections import deque
from pathlib import Path

CORE_ROOTS = frozenset({"app.services.task_preparation", "app.services.task_contracts", "app.services.task_plans"})
# The config dependency is real: collection.intent reads configured limits.
# Neither all of app.core nor all of app.agent is assigned a blanket layer.
EXPECTED_IMPORTS = {
    "app": set(),
    "app.core": set(),
    "app.core.config": {"app.core.settings_views"},
    "app.core.settings_views": set(),
    "app.services": set(),
    "app.services.collection": set(),
    "app.services.collection.intent": {"app.core.config"},
    "app.services.social.vk_access": set(),
    "app.services.task_contracts": {"app.services.task_plans"},
    "app.services.task_plans": {"app.services.collection.intent", "app.services.social.vk_access"},
    "app.services.task_preparation": {"app.services.task_plans"},
}
# Explicit per-module third-party dependencies, never an app-wide exemption.
EXTERNAL_IMPORTS = {
    "app.core.config": {"dotenv", "pydantic", "pydantic_settings"},
    "app.core.settings_views": {"pydantic"},
    "app.services.task_contracts": {"pydantic"},
}


def module_paths(root: Path) -> dict[str, Path]:
    modules = {}
    for path in (root / "app").rglob("*.py"):
        parts = list(path.relative_to(root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        modules[".".join(parts)] = path
    return modules


def relative_name(module: str, path: Path, level: int, name: str) -> str:
    if not level:
        return name
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    parts = package.split(".") if package else []
    if level > len(parts):
        raise AssertionError(f"Relative import escapes package: {module}")
    parts = parts[: len(parts) - level + 1]
    return ".".join([*parts, name] if name else parts)


def imports(module: str, path: Path, modules: dict[str, Path]) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = set()
    dynamic_functions = {"__import__"}
    dynamic_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
                if alias.name in {"importlib", "builtins"}:
                    dynamic_modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = relative_name(module, path, node.level, node.module or "")
            names.add(base)
            for alias in node.names:
                child = f"{base}.{alias.name}"
                if child in modules:
                    names.add(child)
                if base in {"importlib", "builtins"} and alias.name in {"import_module", "__import__"}:
                    dynamic_functions.add(alias.asname or alias.name)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dynamic = isinstance(node.func, ast.Name) and node.func.id in dynamic_functions
        dynamic = dynamic or (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in dynamic_modules
            and node.func.attr in {"import_module", "__import__"}
        )
        if not dynamic:
            continue
        target = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "name"), None)
        if not isinstance(target, ast.Constant) or not isinstance(target.value, str):
            raise AssertionError(f"Unreviewed dynamic import in {module}:{node.lineno}")
        name = target.value
        if name.startswith("."):
            # Resolve only an explicit literal package; computed packages need review.
            package = (
                node.args[1]
                if len(node.args) > 1
                else next((k.value for k in node.keywords if k.arg == "package"), None)
            )
            if not isinstance(package, ast.Constant) or not isinstance(package.value, str):
                raise AssertionError(f"Unreviewed relative dynamic import in {module}:{node.lineno}")
            level = len(name) - len(name.lstrip("."))
            name = relative_name(package.value, Path("__init__.py"), level, name.lstrip("."))
        names.add(name)
        fromlist = next((k.value for k in node.keywords if k.arg == "fromlist"), None)
        if fromlist is None and len(node.args) > 3:
            fromlist = node.args[3]
        if fromlist is not None:
            if not isinstance(fromlist, (ast.List, ast.Tuple)) or not all(
                isinstance(item, ast.Constant) and isinstance(item.value, str) for item in fromlist.elts
            ):
                raise AssertionError(f"Unreviewed dynamic import fromlist in {module}:{node.lineno}")
            for item in fromlist.elts:
                child = f"{name}.{item.value}"
                if child in modules or any(key.startswith(child + ".") for key in modules):
                    names.add(child)

    local = set()
    for name in names:
        top = name.split(".")[0]
        if top == "app":
            # Keep unresolved app paths visible: a new module must not disappear
            # from the graph just because it was not indexed or shipped yet.
            local.add(name)
        elif top not in sys.stdlib_module_names and top not in EXTERNAL_IMPORTS.get(module, set()):
            raise AssertionError(f"Unreviewed external import in {module}: {name}")
    return local


def dependency_graph(root: Path, roots=CORE_ROOTS) -> dict[str, set[str]]:
    modules = module_paths(root)
    graph = {}
    pending = deque(sorted(roots))
    while pending:
        module = pending.popleft()
        if module in graph:
            continue
        if module not in modules:
            if not root.joinpath(*module.split(".")).is_dir():
                raise AssertionError(f"Missing application module: {module}")
            # Namespace packages have no code, but their parents can still run.
            graph[module] = set()
        else:
            graph[module] = imports(module, modules[module], modules)
        # Python executes each existing parent initializer too. Namespace
        # packages have no initializer, but their existing ancestors still do.
        parts = module.split(".")
        parents = {".".join(parts[:i]) for i in range(1, len(parts))}
        pending.extend(sorted(graph[module] | (parents & modules.keys())))
    return graph


def check_contract(root: Path) -> dict[str, set[str]]:
    graph = dependency_graph(root)
    added = {module: sorted(edges - EXPECTED_IMPORTS.get(module, set())) for module, edges in graph.items()}
    added = {module: edges for module, edges in added.items() if edges or module not in EXPECTED_IMPORTS}
    missing = set(EXPECTED_IMPORTS) - graph.keys()
    changed = {module for module in graph.keys() & EXPECTED_IMPORTS.keys() if graph[module] != EXPECTED_IMPORTS[module]}
    assert not (added or missing or changed), (
        f"Task-plan core import contract changed; review edges before updating baseline. "
        f"Added={added}; missing={sorted(missing)}; changed={sorted(changed)}"
    )
    return graph


if __name__ == "__main__":
    result = check_contract(Path(__file__).resolve().parents[2])
    print(f"Task-plan core import contract: PASS ({len(result)} modules)")
