"""Stdlib-only reference rendering and live relocation contracts; no app fixtures."""

import ast
import importlib.util
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("reference_tables", ROOT / "scripts/reference/generate.py")
REFERENCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REFERENCE)


def metadata():
    return {
        "openapi": {
            "paths": {
                "/z": {"post": {"operationId": "write_z"}, "parameters": []},
                "/a": {"get": {"operationId": "read_a"}},
            }
        },
        "cli": [
            {
                "path": "cli z",
                "help": "line one\nline | two",
                "params": [
                    {"name": "--value", "kind": "option", "type": "text", "required": "no", "default": "<dynamic>"},
                ],
            },
            {"path": "cli a", "help": "read", "params": []},
        ],
    }


def test_reference_tables_have_stable_order_and_no_policy_inference():
    result = REFERENCE.render(metadata())
    assert set(result) == set(REFERENCE.OUTPUTS)
    assert result["api-routes.md"].index("GET | /a") < result["api-routes.md"].index("POST | /z")
    assert "parameters" not in result["api-routes.md"]
    assert result["cli-commands.md"].index("cli a") < result["cli-commands.md"].index("cli z")
    assert "line one line \\| two" in result["cli-commands.md"]
    assert "<dynamic>" in result["cli-options.md"]
    assert all("Metadata navigation only" in content for content in result.values())
    assert REFERENCE.render(metadata()) == result


def test_only_stable_tables_are_generated():
    assert REFERENCE.OUTPUTS == ("api-routes.md", "cli-commands.md", "cli-options.md")
    tree = ast.parse((ROOT / "scripts/reference/probe.py").read_text())
    calls = {
        node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert {"openapi", "write_text"} <= calls
    assert {"invoke", "invoke_without_command", "get_default"}.isdisjoint(calls)
    assert "model_dump" not in calls  # Do not export Settings/secrets into references.


@pytest.mark.parametrize("name", ["settings_layout_fixtures.py", "settings_llm_fixtures.py"])
def test_shared_renderers_resolve_the_checkout_not_the_old_root(name):
    path = ROOT / "tests/shared" / name
    tree = ast.parse(path.read_text())
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "ROOT" for target in node.targets)
    )
    assert ast.unparse(assignment.value) == "Path(__file__).resolve().parents[2]"
    assert not (ROOT / "tests" / name).exists()


@pytest.mark.parametrize(
    "folder,name",
    [
        ("browser", "web_confirm_smoke.cjs"),
        ("browser", "web_confirm_keyboard.cjs"),
        ("js", "web_confirm.test.cjs"),
        ("js", "web_shell_state.cjs"),
    ],
)
def test_selected_js_assets_have_one_live_location(folder, name):
    path = ROOT / "tests" / folder / name
    assert path.is_file()
    assert "__dirname" in path.read_text()
    assert not (ROOT / "tests" / name).exists()


def test_current_navigation_links_resolve_without_rewriting_history():
    documents = [
        ROOT / "tests/README.md",
        ROOT / "tests/shared/README.md",
        ROOT / "docs/generated/README.md",
        ROOT / "docs/DOCS_INDEX.md",
    ]
    for document in documents:
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", document.read_text()):
            url = urlsplit(target)
            if url.scheme or target.startswith("#"):
                continue
            destination = (document.parent / unquote(url.path)).resolve()
            assert destination.exists(), (document.relative_to(ROOT), target)
            if url.fragment and destination.suffix == ".md":
                headings = {
                    re.sub(r"[^\w -]", "", line.lstrip("# ").strip().lower()).replace(" ", "-")
                    for line in destination.read_text().splitlines()
                    if line.startswith("#")
                }
                assert unquote(url.fragment) in headings, target


def test_selected_discovery_cannot_be_used_as_an_execution_runner():
    source = (ROOT / "tests/tooling/test_p11_t16_discovery.py").read_text()
    assert '"--collect-only"' in source and '"--noconftest"' in source
    assert '"PYTEST_DISABLE_PLUGIN_AUTOLOAD"' in source
    assert "NoRealApplicationImports" in source and 'fullname.startswith("app.")' in source
    assert "__test__ = False" in source
    assert "parse_known_args" not in source
