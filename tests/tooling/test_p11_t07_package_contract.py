"""Source-only contract plus adversarial fixtures; no app/DB imports."""

import ast
from pathlib import Path

import pytest

from scripts.ci.package_boundaries import check_contract, dependency_graph

ROOT = Path(__file__).resolve().parents[2]


def _sources(root, files):
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    return root


def test_task_plan_core_imports_match_the_reviewed_contract():
    check_contract(ROOT)


@pytest.mark.parametrize(
    "source",
    [
        "import app.api.endpoint",
        "from app.api import endpoint",
        "from ..api import endpoint",
        "def later():\n    from app.api import endpoint",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import app.api.endpoint",
        "import importlib\nimportlib.import_module('app.api.endpoint')",
        "import importlib as loader\nloader.import_module('app.api.endpoint')",
        "from importlib import import_module as load\nload('app.api.endpoint')",
        "__import__('app.api.endpoint')",
        "__import__('app.api', fromlist=['endpoint'])",
        "from builtins import __import__ as load\nload('app.api.endpoint')",
        "import importlib\nimportlib.import_module('.endpoint', 'app.api')",
    ],
)
def test_import_forms_cannot_hide_an_adapter_dependency(tmp_path, source):
    _sources(tmp_path, {"app/services/core.py": source, "app/api/endpoint.py": ""})
    graph = dependency_graph(tmp_path, {"app.services.core"})
    assert "app.api.endpoint" in graph["app.services.core"]


def test_parent_initializers_are_part_of_the_transitive_boundary(tmp_path):
    _sources(
        tmp_path,
        {
            "app/__init__.py": "from app import adapter",
            "app/adapter.py": "",
            "app/services/__init__.py": "from app.services import helper",
            "app/services/helper.py": "",
            "app/services/core.py": "",
        },
    )
    graph = dependency_graph(tmp_path, {"app.services.core"})
    assert graph["app"] == {"app", "app.adapter"}
    assert graph["app.services"] == {"app.services", "app.services.helper"}


def test_transitive_and_cyclic_edges_remain_visible(tmp_path):
    _sources(
        tmp_path,
        {
            "app/core.py": "from app import helper",
            "app/helper.py": "from app import core\nfrom app import adapter",
            "app/adapter.py": "",
            "app/__init__.py": "",
        },
    )
    graph = dependency_graph(tmp_path, {"app.core"})
    assert "app.adapter" in graph["app.helper"]
    assert "app.core" in graph["app.helper"]
    assert set(graph) == {"app", "app.core", "app.helper", "app.adapter"}


@pytest.mark.parametrize(
    "source",
    [
        "__import__(name)",
        "import importlib as loader\nloader.import_module(name)",
        "from importlib import import_module as load\nload(name)",
        "import importlib\nimportlib.import_module('.adapter', package)",
    ],
)
def test_computed_dynamic_imports_require_review(tmp_path, source):
    _sources(tmp_path, {"app/core.py": source})
    with pytest.raises(AssertionError, match="Unreviewed .*import"):
        dependency_graph(tmp_path, {"app.core"})


@pytest.mark.parametrize("target", ["sqlalchemy", "httpx", "telethon", "celery", "segno"])
def test_new_external_runtime_or_optional_dependency_requires_review(tmp_path, target):
    _sources(tmp_path, {"app/core.py": f"import {target}"})
    with pytest.raises(AssertionError, match="Unreviewed external import"):
        dependency_graph(tmp_path, {"app.core"})


def test_missing_local_import_is_not_silently_ignored(tmp_path):
    _sources(tmp_path, {"app/core.py": "import app.missing"})
    with pytest.raises(AssertionError, match="Missing application module: app.missing"):
        dependency_graph(tmp_path, {"app.core"})


def test_lazy_optional_provider_import_outside_core_is_not_banned(tmp_path):
    _sources(
        tmp_path,
        {"app/core.py": "", "app/provider.py": "def client():\n    import telethon"},
    )
    assert dependency_graph(tmp_path, {"app.core"}) == {"app.core": set()}


def test_core_cannot_reach_persistence_shell():
    graph = check_contract(ROOT)
    assert "app.services.task_preparation" in graph
    assert "app.services.task_creation" not in graph
    assert not any(name.startswith(("app.models", "app.agent", "app.channels", "app.api")) for name in graph)


def test_compatibility_exports_and_lazy_persistence_are_explicit():
    tree = ast.parse((ROOT / "app/services/task_creation.py").read_text())
    exports = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "app.services.task_preparation"
        for alias in node.names
    }
    assert exports == {
        "TaskPlanCreate",
        "TaskPlanCreateConflict",
        "prepare_task_plan_create",
        "project_task_plan_create",
        "resolve_task_plan_create",
    }
    # TYPE_CHECKING model references and function-local manager imports remain
    # legal in this persistence shell, but eager runtime ORM imports do not.
    eager = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert all(not isinstance(node, ast.Import) for node in eager)
    assert {node.module for node in eager} == {"__future__", "typing", "app.services.task_preparation"}


@pytest.mark.parametrize(
    ("path", "extra"),
    [
        ("app/services/task_preparation.py", "\nimport app.api.endpoint\n"),
        ("app/services/task_plans.py", "\nfrom app.api import endpoint  # noqa\n"),
        ("app/services/__init__.py", "\nimport app.api.endpoint\n"),
        ("app/core/settings_views.py", "\nimport app.services.task_preparation\n"),
        ("app/services/task_preparation.py", "\nimport telethon\n"),
    ],
)
def test_actual_contract_rejects_adapter_parent_cycle_and_provider_drift(tmp_path, path, extra):
    # Copy only the reviewed closure, never import it or its config.
    from scripts.ci.package_boundaries import module_paths

    modules = module_paths(ROOT)
    for module in check_contract(ROOT):
        source = modules[module]
        _sources(tmp_path, {str(source.relative_to(ROOT)): source.read_text()})
    _sources(tmp_path, {"app/api/__init__.py": "", "app/api/endpoint.py": ""})
    target = tmp_path / path
    target.write_text(target.read_text() + extra)
    with pytest.raises(AssertionError, match="contract changed|Unreviewed external import"):
        check_contract(tmp_path)


def test_namespace_import_does_not_skip_its_parent_initializer(tmp_path):
    _sources(
        tmp_path,
        {
            "app/core.py": "import app.providers.namespace",
            "app/providers/__init__.py": "import app.adapter",
            "app/providers/namespace/helper.py": "",
            "app/adapter.py": "",
        },
    )
    graph = dependency_graph(tmp_path, {"app.core"})
    assert graph["app.providers"] == {"app.adapter"}
    assert "app.adapter" in graph


def test_computed_dynamic_fromlist_requires_review(tmp_path):
    _sources(tmp_path, {"app/core.py": "__import__('app', fromlist=names)"})
    with pytest.raises(AssertionError, match="Unreviewed dynamic import fromlist"):
        dependency_graph(tmp_path, {"app.core"})
