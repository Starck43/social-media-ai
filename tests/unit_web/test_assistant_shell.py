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


def test_invite_buttons_expose_dialog_target_in_both_shells():
    markup = environment().get_template("shell-fixture.html").render(**context(manager=True))
    invites = [attrs for tag, attrs in Tags(markup).tags if "data-invite-open" in attrs]
    assert len(invites) == 2
    assert all(
        attrs.get("aria-controls") == "invite-dialog" and attrs.get("aria-haspopup") == "dialog" for attrs in invites
    )


@pytest.mark.parametrize("operator", [True, False])
def test_platform_admin_link_keeps_operator_gate(operator):
    markup = environment().get_template("shell-fixture.html").render(**context(operator=operator))
    assert ('href="/admin/"' in markup) is operator


def test_workspace_switch_keeps_post_and_csrf():
    values = context(manager=True)
    values["workspaces"] = [SimpleNamespace(active=False, tenant=SimpleNamespace(id=8, name="Other"), role="Viewer")]
    markup = environment().get_template("shell-fixture.html").render(**values)
    assert 'method="post" action="/app/workspaces/select"' in markup
    assert 'name="tenant_id" value="8"' in markup
    assert 'name="_csrf" value="csrf&#34;&lt;token&gt;"' in markup


def test_workspace_group_has_explicit_switch_target_and_no_daily_join_for_members():
    values = context(manager=True)
    values["workspaces"] = [
        SimpleNamespace(
            active=True, tenant=SimpleNamespace(id=7, name="Current"), role="SUPERUSER", role_label="Владелец"
        ),
        SimpleNamespace(
            active=False, tenant=SimpleNamespace(id=8, name="Other <script>"), role="MANAGER", role_label="Менеджер"
        ),
    ]
    markup = environment().get_template("shell-fixture.html").render(**values)
    assert 'aria-labelledby="workspaces-desktop"' in markup
    assert 'aria-label="Переключиться в пространство «Other &lt;script&gt;»"' in markup
    assert "Переключиться · Менеджер" in markup
    assert 'href="/app/invite"' not in markup
    assert 'action="/app/workspaces/select"' in markup
    assert 'name="tenant_id" value="8"' in markup
    assert 'action="/app/logout"' in markup


@pytest.mark.parametrize("manager", [True, False])
def test_team_exposes_join_without_granting_invite_issuance(manager):
    values = context(manager=manager)
    values.update(
        tab="team", visible_tabs=["team"], memberships=[], member_names={}, member_role_keys={}, role_labels={}
    )
    markup = environment().get_template("web/settings.html").render(**values)
    assert "data-team-join" in markup
    assert 'href="/app/invite"' in markup
    assert ("data-invite-open" in markup) is manager


def test_theme_and_collapse_leave_navbar_and_keep_unique_control_targets():
    markup = environment().get_template("shell-fixture.html").render(**context())
    tags = Tags(markup).tags
    collapse = [attrs for _, attrs in tags if "data-sidebar-collapse" in attrs]
    assert len(collapse) == 1
    assert collapse[0]["aria-controls"] == "nav-desktop"
    assert "theme-popover-desktop" in markup and "theme-popover-mobile" in markup
    assert "theme-popover-guest" not in markup
    header = markup.split('<header class="shell-navbar">', 1)[1].split("</header>", 1)[0]
    assert "Тема оформления" not in header
    assert "shell-sidebar-footer" in markup


@pytest.mark.parametrize(
    "codename,role_id,expected",
    [
        ("SUPERUSER", 1, "Владелец"),
        ("MANAGER", 1, "Менеджер"),
        ("UNKNOWN", 1, "Роль не назначена"),
        (None, None, "Владелец"),
    ],
)
def test_workspace_role_caption_reuses_canonical_labels_without_app_imports(codename, role_id, expected):
    import runpy

    # This inspected module imports only dataclasses/typing, not app/runtime/DB.
    roles = runpy.run_path(str(ROOT / "app/services/tenancy/member_roles.py"))
    tree = ast.parse((ROOT / "app/web/middleware.py").read_text())
    assignment = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "level" for t in n.targets)
    )
    role = SimpleNamespace(codename=SimpleNamespace(name=codename)) if codename else None
    scope = dict(
        member_roles=SimpleNamespace(resolve=roles["resolve"], key_of=roles["key_of"]),
        membership=SimpleNamespace(role=role, role_id=role_id),
    )
    scope["level"] = eval(compile(ast.Expression(assignment.value), "workspace role caption", "eval"), scope)
    field = next(
        value
        for n in ast.walk(tree)
        if isinstance(n, ast.Dict)
        for key, value in zip(n.keys, n.values)
        if isinstance(key, ast.Constant) and key.value == "role_label"
    )
    assert eval(compile(ast.Expression(field), "workspace role label", "eval"), scope) == expected


@pytest.mark.parametrize("manager", [True, False])
def test_workspace_actions_are_paired_before_switcher_in_both_menus(manager):
    markup = environment().get_template("shell-fixture.html").render(**context(manager=manager))
    for menu in ("desktop", "mobile"):
        panel = markup.split(f'id="account-{menu}"', 1)[1].split("</nav>", 1)[0]
        tools, switcher = panel.split(f'aria-labelledby="workspaces-{menu}"', 1)
        assert f'aria-labelledby="workspace-tools-{menu}"' in tools
        actions = tools.split('<div class="shell-workspace-actions">', 1)[1].split("</div>", 1)[0]
        assert actions.count('href="/app/settings"') == 1
        assert ("data-invite-open" in actions) is manager
        if manager:
            assert actions.index('href="/app/settings"') < actions.index("data-invite-open")
        assert 'href="/app/settings"' not in switcher
        assert "data-invite-open" not in switcher


def test_no_workspace_does_not_render_workspace_action_pair():
    markup = environment().get_template("shell-fixture.html").render(**context(tenant=False))
    assert 'class="shell-workspace-actions"' not in markup
    assert 'href="/app/invite"' in markup
