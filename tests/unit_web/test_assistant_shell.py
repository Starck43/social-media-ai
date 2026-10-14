"""Isolated shell contracts. Remote no-DB skill exception; no application imports or DB."""

from __future__ import annotations

import ast
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, StrictUndefined, select_autoescape

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "app/web/templates"


class Tags(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.tags = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def environment():
    env = Environment(
        loader=ChoiceLoader(
            [
                DictLoader(
                    {"shell-fixture.html": '{% extends "web/base.html" %}{% block page_heading %}Fixture{% endblock %}'}
                ),
                FileSystemLoader(WEB),
            ]
        ),
        autoescape=select_autoescape(),
        undefined=StrictUndefined,
    )
    env.filters["ui_label"] = str
    env.filters["human_dt"] = lambda value, **kwargs: str(value or "")
    env.globals.update(url_for=lambda name, path: "/static/" + path, static_v=lambda path: "/static/" + path)
    return env


def context(*, manager=False, tenant=True, operator=False):
    return dict(
        user=SimpleNamespace(username="<script>stored</script>", is_superuser=operator),
        tenant=SimpleNamespace(id=7, name='<img src=x onerror="bad()">') if tenant else None,
        request=SimpleNamespace(query_params={}),
        section="dashboard",
        csrf='csrf"<token>',
        flashes=[],
        connection_alerts=[],
        member_level=None,
        perms=SimpleNamespace(
            can_manage_workspace=lambda target: manager and target == 7, workspace_role="MANAGER", is_owner=manager
        ),
        nav=[SimpleNamespace(key="dashboard", href="/app/", ready=True, label="Обзор", icon="home")],
        workspaces=[],
    )


def test_all_base_children_supply_a_heading_and_no_content_h1():
    for path in WEB.joinpath("web").glob("*.html"):
        source = path.read_text()
        if '{% extends "web/base.html" %}' not in source:
            continue
        assert "{% block page_heading %}" in source, path
        assert "<h1" not in source and "{{ page_title(" not in source, path


@pytest.mark.parametrize("manager", [True, False])
def test_menu_is_content_gated_and_stored_values_are_inert(manager):
    markup = environment().get_template("shell-fixture.html").render(**context(manager=manager))
    tags = Tags(markup)
    assert sum(tag == "h1" for tag, _ in tags.tags) == 1
    assert not any(tag in {"script", "img"} and attrs.get("src") == "x" for tag, attrs in tags.tags)
    assert "&lt;script&gt;stored&lt;/script&gt;" in markup
    assert ("data-invite-open" in markup) is manager
    assert ('id="invite-dialog"' in markup) is manager
    assert 'action="/app/settings/invites"' not in markup  # lazy, never preloaded
    assert "data-theme-select" not in markup
    assert 'data-theme-choice="system"' in markup
    assert 'id="context-panel"' not in markup
    assert 'action="/app/logout"' in markup
    assert 'name="_csrf"' in markup
    assert 'href="/admin/"' not in markup


def test_no_workspace_has_account_utilities_but_no_workspace_data_or_invite_fetch():
    markup = environment().get_template("shell-fixture.html").render(**context(tenant=False))
    assert 'action="/app/workspaces/new"' in markup
    assert 'href="/app/invite"' in markup
    assert "notificationsBell(" not in markup
    assert "data-invite-dialog" not in markup


def test_right_panel_only_exists_for_supplied_content():
    env = environment()
    source = '{% extends "web/base.html" %}{% block right_panel %}<p>Authorized fixture only</p>{% endblock %}'
    markup = env.from_string(source).render(**context())
    assert 'data-dialog-open="context-panel"' in markup
    assert 'id="context-panel"' in markup
    assert "Authorized fixture only" in markup


def functions(filename, *names):
    tree = ast.parse((ROOT / filename).read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    for node in nodes:
        node.decorator_list = []
    scope = {}
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(
                    body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes],
                    type_ignores=[],
                )
            ),
            filename,
            "exec",
        ),
        scope,
    )
    return scope


@pytest.mark.asyncio
async def test_invite_partial_checks_capability_before_any_query_or_one_time_pop():
    scope = functions("app/web/settings.py", "invite_dialog")
    query = AsyncMock(side_effect=AssertionError("forbidden invite query"))
    scope.update(HTMLResponse=lambda body, status_code: SimpleNamespace(status_code=status_code), _open_invites=query)
    request = SimpleNamespace(
        state=SimpleNamespace(tenant_id=7, web_perms=SimpleNamespace(can_manage_workspace=lambda target: False))
    )
    assert (await scope["invite_dialog"](request)).status_code == 403
    query.assert_not_awaited()


@pytest.mark.asyncio
async def test_invite_partial_only_loads_active_target_and_disables_cache():
    scope = functions("app/web/settings.py", "invite_dialog")
    query = AsyncMock(return_value=[])
    response = SimpleNamespace(headers={})
    scope.update(
        _open_invites=query,
        _pop_issued_invite=lambda req: None,
        member_roles=SimpleNamespace(INVITABLE=[], DEFAULT_INVITE_KEY="viewer"),
        INVITE_DAYS=(1, 7, 30),
        render=lambda *args, **kwargs: response,
    )
    request = SimpleNamespace(
        state=SimpleNamespace(tenant_id=7, web_perms=SimpleNamespace(can_manage_workspace=lambda target: target == 7))
    )
    assert await scope["invite_dialog"](request) is response
    query.assert_awaited_once_with(request, 7, ("team",))
    assert response.headers["Cache-Control"] == "no-store"


def test_one_time_code_is_bound_to_issuing_workspace():
    scope = functions("app/web/settings.py", "_pop_issued_invite")
    scope.update(_ISSUED_INVITE_KEY="_issued_invite", invite_qr_svg=lambda link: None)
    request = SimpleNamespace(
        state=SimpleNamespace(tenant_id=8),
        session={"_issued_invite": {"code": "ABC", "tenant_id": 7}},
        base_url="http://fixture/",
    )
    assert scope["_pop_issued_invite"](request) is None
    assert "_issued_invite" not in request.session
