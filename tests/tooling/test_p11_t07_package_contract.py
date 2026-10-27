"""Reviewed stdlib-only dependency contract for the task-plan core."""

import ast
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE_ROOTS = {
    "app.services.task_contracts",
    "app.services.task_plans",
}

# This deliberately scopes the first boundary to the pure plan/contract core.
# app.core.config is an explicit dependency; this does not classify all of
# app.core as a pure bottom layer, nor does it classify all of app.agent.
EXPECTED_IMPORTS = {
    "app.core.config": {"app.core.settings_views"},
    "app.core.settings_views": set(),
    "app.services.collection.intent": {"app.core.config"},
    "app.services.social.vk_access": set(),
    "app.services.task_contracts": {"app.services.task_plans"},
    "app.services.task_plans": {
        "app.services.collection.intent",
        "app.services.social.vk_access",
    },
}


def _module_paths():
    modules = {}
    for path in (ROOT / "app").rglob("*.py"):
        parts = list(path.relative_to(ROOT).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        modules[".".join(parts)] = path
    return modules


def _relative_base(module, level, name, modules):
    if not level:
        return name
    path = modules[module]
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    parts = package.split(".") if package else []
    if level > 1:
        parts = parts[: -(level - 1)]
    if name:
        parts.extend(name.split("."))
    return ".".join(parts)


def _app_imports(module, path, modules):
    tree = ast.parse(path.read_text())
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names if alias.name in modules)
        elif isinstance(node, ast.ImportFrom):
            base = _relative_base(module, node.level, node.module or "", modules)
            if base in modules:
                imports.add(base)
            for alias in node.names:
                child = f"{base}.{alias.name}" if base else alias.name
                if child in modules:
                    imports.add(child)
        elif isinstance(node, ast.Call):
            dynamic_import = isinstance(node.func, ast.Name) and node.func.id == "__import__"
            dynamic_import = dynamic_import or (
                isinstance(node.func, ast.Attribute) and node.func.attr == "import_module"
            )
            if dynamic_import:
                target = node.args[0] if node.args else None
                if not isinstance(target, ast.Constant) or not isinstance(target.value, str):
                    raise AssertionError(f"Unreviewed dynamic import in {module}:{node.lineno}")
                if target.value in modules:
                    imports.add(target.value)
    return imports


def test_task_plan_core_imports_match_the_reviewed_contract():
    modules = _module_paths()
    missing_roots = CORE_ROOTS - modules.keys()
    assert not missing_roots, f"Task-plan core modules missing: {sorted(missing_roots)}"

    imports = defaultdict(set)
    pending = deque(CORE_ROOTS)
    visited = set()
    while pending:
        module = pending.popleft()
        if module in visited:
            continue
        visited.add(module)
        imports[module] = _app_imports(module, modules[module], modules)
        pending.extend(imports[module] - visited)

    assert visited == EXPECTED_IMPORTS.keys(), (
        "The task-plan core dependency set changed; review each new edge before "
        f"updating the contract. Unexpected={sorted(visited - EXPECTED_IMPORTS.keys())}; "
        f"missing={sorted(EXPECTED_IMPORTS.keys() - visited)}"
    )
    actual = {module: imports[module] for module in EXPECTED_IMPORTS}
    assert actual == EXPECTED_IMPORTS, (
        "The task-plan core import edges changed; review adapter, provider and "
        f"runtime dependencies before updating the contract. Actual={actual}"
    )