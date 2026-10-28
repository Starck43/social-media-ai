"""Settings platforms catalog on extracted handlers and explicit fake ORM boundaries.

This module imports no application module, opens no database session and renders
the catalog template from the checked-in Jinja sources only.
"""

from __future__ import annotations

import ast
import re
import runpy
import sys
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.responses import HTMLResponse, RedirectResponse

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "app/web/settings.py"
USER_SOURCE = ROOT / "app/models/identity/user.py"
TEMPLATE_FIXTURES = runpy.run_path(str(ROOT / "tests/shared/settings_layout_fixtures.py"))
TREE = ast.parse(SOURCE.read_text())


def _module_literal(name: str) -> object:
    node = next(n for n in TREE.body if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == name)
    return ast.literal_eval(node.value)


def _platform_admin_property() -> type:
    """The real active-admin predicate, mounted on a local actor class."""
    tree = ast.parse(USER_SOURCE.read_text())
    user = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "User")
    method = next(n for n in user.body if isinstance(n, ast.FunctionDef) and n.name == "is_platform_admin")
    module = ast.Module(
        body=[ast.ClassDef(name="Actor", bases=[], keywords=[], body=[method], decorator_list=[])], type_ignores=[]
    )
    namespace = {"PlatformRoleType": NS(ADMIN="admin", SUPERUSER="superuser")}
    exec(compile(ast.fix_missing_locations(module), str(USER_SOURCE), "exec"), namespace)
    return namespace["Actor"]


ACTOR = _platform_admin_property()
WORKSPACE_TABS = tuple(tab for tab in _module_literal("TABS") if tab != "platforms")


def extracted() -> dict:
    tree = ast.parse(SOURCE.read_text())
    names = {
        "_personal_access",
        "_platform_access",
        "_workspace_access",
        "_visible_tabs",
        "_visible_tab",
        "_tab_of",
        "settings_page",
        "platform_save",
    }
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    for node in nodes:
        node.decorator_list = []
        node.args.defaults = [ast.Constant(None) for _ in node.args.defaults]
        node.args.kw_defaults = [ast.Constant(None) if n is not None else None for n in node.args.kw_defaults]
    namespace = {
        "TABS": _module_literal("TABS"),
        "MANAGER_TABS": frozenset({"agent", "channels"}),
        "BACK": "/app/settings",
        "HTMLResponse": HTMLResponse,
        "RedirectResponse": RedirectResponse,
        "add_flash": Mock(),
        "ensure_csrf": Mock(return_value=True),
        "render": lambda request, template, **context: NS(headers={}, context=context),
    }
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(
                    body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes],
                    type_ignores=[],
                )
            ),
            str(SOURCE),
            "exec",
        ),
        namespace,
    )
    return namespace


class Perms:
    def __init__(self, manage: bool) -> None:
        self.manage = manage

    def can_manage_workspace(self, target) -> bool:
        return self.manage and target == 7


def request(
    tab: str = "platforms",
    *,
    role: str | None = "admin",
    active: bool = True,
    membership: bool = False,
    manage: bool = False,
):
    user = ACTOR()
    user.id = 101
    user.is_active = active
    user.role = NS(codename=role) if role is not None else None
    tenant = NS(id=7, is_active=True, name="Workspace", timezone="Europe/Kirov")
    member = NS(id=3, user_id=101, tenant_id=7, channel="web", is_active=True)
    return NS(
        state=NS(
            tenant_id=7 if membership else None,
            tenant=tenant if membership else None,
            web_user=user,
            memberships=[member] if membership else [],
            web_perms=Perms(manage),
        ),
        query_params={"tab": tab},
        session={},
    )


class Query:
    def __init__(self, rows, calls) -> None:
        self.rows, self.calls = rows, calls

    def filter(self, **kwargs):
        self.calls.append(("platforms", "filter", kwargs))
        return self

    def order_by(self, column):
        self.calls.append(("platforms", "order_by"))
        return self

    def __await__(self):
        async def rows():
            return self.rows

        return rows().__await__()


class Kinds:
    members = (NS(db_value="vk", label="ВКонтакте"), NS(db_value="telegram", label="Telegram"))

    def __iter__(self):
        return iter(self.members)

    def get_by_value(self, value):
        return next((kind for kind in self.members if kind.db_value == value), None)


@pytest.fixture
def gateway(monkeypatch):
    calls = []

    def filtered(**kwargs):
        calls.append(("platforms", "filter", kwargs))
        return Query([NS(id=7)], calls)

    manager = NS(
        filter=Mock(side_effect=filtered),
        update_by_id=AsyncMock(return_value=NS(id=7)),
        create_platform=AsyncMock(),
        delete_by_id=Mock(side_effect=AssertionError("settings must not delete platforms")),
        delete=Mock(side_effect=AssertionError("settings must not delete platforms")),
    )
    platform_module = ModuleType("app.models.collection.platform")
    platform_module.Platform = NS(objects=manager, id="id")
    types_module = ModuleType("app.types")
    types_module.PlatformType = Kinds()
    monkeypatch.setitem(sys.modules, "app.models.collection.platform", platform_module)
    monkeypatch.setitem(sys.modules, "app.types", types_module)
    namespace = extracted()
    return namespace, manager, calls


async def save(namespace, **overrides):
    values = dict(
        name="AcceptancePlatform",
        base_url="https://api.example.invalid/v1",
        platform_type="telegram",
        platform_id=0,
        enabled="on",
        token="fixture-csrf",
    )
    values.update(overrides)
    return await namespace["platform_save"](request(), **values)


@pytest.mark.parametrize("role", ["admin", "superuser", "operator", "user", "unknown", None])
@pytest.mark.parametrize("active", [True, False])
@pytest.mark.parametrize("membership", [True, False])
@pytest.mark.parametrize("manage", [True, False])
def test_platforms_tab_needs_a_fresh_active_platform_admin(role, active, membership, manage):
    namespace = extracted()
    req = request(role=role, active=active, membership=membership, manage=manage)
    tabs = namespace["_visible_tabs"](req, req.state.tenant_id)
    expected = bool(active and role in ("admin", "superuser"))
    assert ("platforms" in tabs) is expected
    if not active:
        assert tabs == ()
    elif not membership:
        assert tabs == (("account", "models", "platforms") if expected else ("account", "models"))
    elif manage:
        assert tabs == WORKSPACE_TABS + (("platforms",) if expected else ())
    else:
        member_tabs = tuple(tab for tab in WORKSPACE_TABS if tab not in namespace["MANAGER_TABS"])
        assert tabs == member_tabs + (("platforms",) if expected else ())


@pytest.mark.asyncio
async def test_global_catalog_reads_without_workspace_and_loads_nothing_else(gateway):
    namespace, manager, calls = gateway
    response = await namespace["settings_page"](request("platforms", membership=False))
    context = response.context
    assert context["tab"] == "platforms"
    assert context["settings_scope"] == "global"
    assert context["visible_tabs"] == ("account", "models", "platforms")
    assert context["settings_manage"] is False
    assert [row.id for row in context["platforms"]] == [7]
    assert context["platform_type_options"] == [("vk", "ВКонтакте", ""), ("telegram", "Telegram", "")]
    assert response.headers["Cache-Control"] == "no-store"
    assert calls == [("platforms", "filter", {}), ("platforms", "order_by")]
    manager.update_by_id.assert_not_awaited()
    manager.create_platform.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["operator", "user", None])
@pytest.mark.parametrize("active", [True, False])
async def test_non_admin_bookmark_never_reads_the_global_catalog(gateway, role, active):
    namespace, _manager, calls = gateway
    response = await namespace["settings_page"](request("platforms", role=role, active=active))
    if not active:
        # An inactive actor is denied before tab resolution, so no render context exists.
        assert response.status_code == 403
        assert not hasattr(response, "context")
    else:
        assert response.context["tab"] == "account"
        assert response.context["settings_scope"] == "personal"
        assert "platforms" not in response.context
    assert calls == []


@pytest.mark.asyncio
async def test_admin_keeps_personal_and_workspace_tab_selection(gateway):
    namespace, _manager, calls = gateway
    for tab, scope in (("account", "personal"), ("workspace", "workspace")):
        response = await namespace["settings_page"](request(tab, membership=True))
        assert response.context["tab"] == tab
        assert response.context["settings_scope"] == scope
        assert "platforms" in response.context["visible_tabs"]
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,active", [("on", True), ("", False)])
async def test_create_uses_the_existing_manager_and_only_mutable_input(gateway, enabled, active):
    namespace, manager, _calls = gateway
    response = await save(
        namespace,
        name="  AcceptancePlatform  ",
        base_url="  https://api.example.invalid/v1  ",
        platform_type="telegram",
        enabled=enabled,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/app/settings?tab=platforms"
    manager.create_platform.assert_awaited_once()
    kwargs = manager.create_platform.call_args.kwargs
    assert set(kwargs) == {"name", "base_url", "platform_type", "is_active"}
    assert kwargs["name"] == "AcceptancePlatform"
    assert kwargs["base_url"] == "https://api.example.invalid/v1"
    assert kwargs["platform_type"].db_value == "telegram"
    assert kwargs["is_active"] is active
    manager.update_by_id.assert_not_awaited()
    assert namespace["add_flash"].call_args.args[1:] == ("success", "Платформа сохранена")


@pytest.mark.asyncio
async def test_update_changes_only_name_address_and_activation(gateway):
    namespace, manager, _calls = gateway
    response = await save(
        namespace,
        platform_id=7,
        platform_type="telegram",
        name="RenamedPlatform",
        base_url="https://api.example.invalid/v2",
        enabled="",
    )
    assert response.status_code == 302
    manager.update_by_id.assert_awaited_once_with(
        7, name="RenamedPlatform", base_url="https://api.example.invalid/v2", is_active=False
    )
    assert set(manager.update_by_id.call_args.kwargs) == {"name", "base_url", "is_active"}
    manager.create_platform.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"name": " "},
        {"name": "x" * 51},
        {"base_url": "javascript:alert(1)"},
        {"base_url": "ftp://example.invalid/resource"},
        {"base_url": "https://user:password@example.invalid"},
        {"base_url": "https://example.invalid:bad"},
        {"base_url": "https://[bad"},
        {"base_url": "https://example.invalid/" + "x" * 240},
        {"platform_id": -1},
        {"platform_id": 2_147_483_648},
    ],
)
async def test_invalid_values_are_refused_before_any_storage(gateway, overrides):
    namespace, manager, _calls = gateway
    response = await save(namespace, **overrides)
    assert response.status_code == 302
    assert response.headers["location"] == "/app/settings?tab=platforms"
    namespace["add_flash"].assert_called_once()
    assert namespace["add_flash"].call_args.args[1] == "error"
    manager.create_platform.assert_not_awaited()
    manager.update_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_type_and_duplicate_name_refuse_without_leaking_details(gateway):
    from sqlalchemy.exc import IntegrityError

    namespace, manager, _calls = gateway
    assert (await save(namespace, platform_type="unknown")).status_code == 302
    manager.create_platform.assert_not_awaited()

    manager.create_platform.side_effect = IntegrityError("insert", {}, Exception("private catalog detail"))
    response = await save(namespace)
    assert response.status_code == 302
    assert "private catalog detail" not in str(namespace["add_flash"].call_args)
    assert namespace["add_flash"].call_args.args[1] == "error"


@pytest.mark.asyncio
async def test_missing_platform_row_answers_not_found_without_writes(gateway):
    namespace, manager, _calls = gateway
    manager.update_by_id.return_value = None
    response = await save(namespace, platform_id=2_147_483_647)
    assert response.status_code == 404
    manager.update_by_id.assert_awaited_once_with(
        2_147_483_647, name="AcceptancePlatform", base_url="https://api.example.invalid/v1", is_active=True
    )
    manager.create_platform.assert_not_awaited()


def test_platform_tab_template_keeps_bounded_fields_and_no_delete_action():
    context = TEMPLATE_FIXTURES["context"]("owner-workspace")
    context.update(
        tab="platforms",
        visible_tabs=["account", "platforms"],
        platforms=[
            NS(
                id=7,
                name="<script>catalog()</script>",
                base_url="https://api.example.invalid/" + "path" * 50,
                is_active=True,
                platform_type=NS(label="ВКонтакте"),
            ),
            NS(
                id=8,
                name="Отключённая платформа",
                base_url="https://api.example.invalid/v2",
                is_active=False,
                platform_type=NS(label="Telegram"),
            ),
        ],
        platform_type_options=[("vk", "ВКонтакте", ""), ("telegram", "Telegram", "")],
    )
    html = TEMPLATE_FIXTURES["environment"]().get_template("web/settings.html").render(**context)
    forms = re.findall(r'<form method="post" action="/app/settings/platforms".*?</form>', html, re.S)
    assert len(forms) == 3
    assert all(form.count('name="_csrf"') == 1 for form in forms)
    assert sum('name="platform_id"' in form for form in forms) == 2
    assert sum('name="platform_type"' in form for form in forms) == 1
    assert html.count('maxlength="50"') == 3 and html.count('maxlength="255"') == 3
    assert html.count('name="enabled" checked') == 2
    assert "<script>catalog()" not in html and "&lt;script&gt;catalog()" in html
    assert "data-popover-select" in html and "<select" not in html
    assert "params" not in html
    for forbidden in ("/delete", "/admin/platform", "platform-delete"):
        assert forbidden not in html
    assert "Удаление здесь недоступно" in html


def test_settings_routes_expose_no_platform_delete_operation():
    routes = []
    for node in TREE.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            owner = decorator.func.value
            if not isinstance(owner, ast.Name) or owner.id != "router" or not decorator.args:
                continue
            routes.append((decorator.func.attr, decorator.args[0].value))
    assert ("post", "/platforms") in routes
    assert not [route for route in routes if route[0] in {"delete", "put", "patch"} and "platform" in route[1]]
