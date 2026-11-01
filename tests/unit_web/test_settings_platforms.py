"""Regression: global platform management is absent from personal web settings.

Extracted handlers and templates only; no application imports, DB or network.
"""

from __future__ import annotations

import ast
import runpy
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

__source_only__ = True
ROOT = Path(__file__).resolve().parents[2]
TREE = ast.parse((ROOT / "app/web/settings.py").read_text())


def policy():
    names = {"_personal_access", "_workspace_access", "_visible_tabs", "_visible_tab", "_tab_of"}
    nodes = [n for n in TREE.body if getattr(n, "name", "") in names]
    ns = {"MANAGER_TABS": frozenset({"agent", "channels"})}
    for node in TREE.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "TABS":
            ns["TABS"] = ast.literal_eval(node.value)
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), "settings policy", "exec"), ns)
    return ns


@pytest.mark.parametrize("admin", [True, False])
@pytest.mark.parametrize("member,manage", [(False, False), (True, False), (True, True)])
def test_no_actor_receives_platform_settings(admin, member, manage):
    ns = policy()
    request = NS(
        query_params={"tab": "platforms"},
        state=NS(
            web_user=NS(id=10, is_active=True, is_platform_admin=admin),
            tenant=NS(id=1, is_active=True),
            memberships=[NS(tenant_id=1, user_id=10, channel="web", is_active=True)] if member else [],
            web_perms=NS(can_manage_workspace=lambda _: manage),
        ),
    )
    tabs = ns["_visible_tabs"](request, 1)
    assert "platforms" not in tabs
    assert ns["_visible_tab"](request, 1, tabs) in tabs
    assert ns["_visible_tab"](request, 1, tabs) != "platforms"
    assert "account" in tabs and "models" in tabs
    assert ("channels" in tabs) is (member and manage)


def test_removed_write_handler_and_catalog_reads():
    assert not any(getattr(n, "name", "") == "platform_save" for n in TREE.body)
    for node in ast.walk(TREE):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"get", "post", "put", "delete", "patch"} and node.args:
                assert getattr(node.args[0], "value", None) != "/platforms"
    source = (ROOT / "app/web/settings.py").read_text()
    assert "Platform.objects" not in source


def test_settings_template_has_no_platform_form_or_navigation():
    fixtures = runpy.run_path(str(ROOT / "tests/shared/settings_layout_fixtures.py"))
    html = fixtures["environment"]().get_template("web/settings.html").render(**fixtures["context"]("owner-workspace"))
    assert "tab=platforms" not in html
    assert "/app/settings/platforms" not in html
    assert "_settings_platforms.html" not in (ROOT / "app/web/templates/web/settings.html").read_text()
