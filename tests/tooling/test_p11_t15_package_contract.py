"""Tool-only declaration/metadata checks; never imports app or DB fixtures."""

import ast
import importlib.util
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("package_dependencies", ROOT / "scripts/packaging/dependencies.py")
DEPS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEPS)


def test_dependency_source_and_every_export_are_in_parity():
    assert DEPS.check() == len(DEPS.PROFILES)


@pytest.mark.parametrize(
    "text",
    ["thing>=1", "thing==1\nthing==2", "thing==1; python_version<'3.12'", "thing @ https://example.com/thing.whl"],
)
def test_lock_format_refuses_ranges_duplicate_pins_and_ambiguous_targets(text):
    with pytest.raises(ValueError):
        DEPS.pins(text)


def test_package_and_api_use_one_side_effect_free_version_source():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "version" not in project["project"]
    assert project["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "app.version.__version__"}
    version = ast.parse((ROOT / "app/version.py").read_text())
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom, ast.Call)) for node in ast.walk(version))
    value = next(node.value.value for node in version.body if isinstance(node, ast.Assign))
    assert len(value.split(".")) == 3 and all(part.isdecimal() for part in value.split("."))
    main = ast.parse((ROOT / "app/main.py").read_text())
    calls = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "FastAPI"
    ]
    assert any(
        keyword.arg == "version" and isinstance(keyword.value, ast.Name) and keyword.value.id == "__version__"
        for call in calls
        for keyword in call.keywords
    )


def test_optional_profiles_do_not_reclassify_the_real_worker_as_celery():
    assert "worker_forever" in (ROOT / "app/worker.py").read_text()
    assert (ROOT / "app/runtime.py").is_file()
    core = DEPS.pins((ROOT / "requirements.txt").read_text())
    assert {"celery", "telethon", "segno", "pytest"}.isdisjoint(core)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert "yourusername" not in str(project) and "Your Name" not in str(project)
    assert project["name"] == "social-media-ai"
