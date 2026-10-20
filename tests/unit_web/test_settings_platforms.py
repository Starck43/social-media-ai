"""Real settings policies/templates with explicit fake ORM boundaries; no DB/ASGI."""

import ast
import runpy
import sys
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.responses import HTMLResponse, RedirectResponse

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = runpy.run_path(str(ROOT / "tests/settings_layout_fixtures.py"))


def extracted():
    tree = ast.parse((ROOT / "app/web/settings.py").read_text())
    names = {
        "_personal_access",
        "_platform_access",
        "_workspace_access",
        "_visible_tabs",
        "_visible_tab",
        "_tab_of",
        "platform_save",
        "settings_page",
    }
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    for node in nodes:
        node.decorator_list = []
        node.args.defaults = [ast.Constant(None) for _ in node.args.defaults]
    ns = dict(
        TABS=ast.literal_eval(
            next(n.value for n in tree.body if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "TABS")
        ),
        MANAGER_TABS={"agent", "channels"},
        BACK="/app/settings",
        HTMLResponse=HTMLResponse,
        RedirectResponse=RedirectResponse,
        ensure_csrf=Mock(return_value=True),
        add_flash=Mock(),
        render=lambda *a, **kw: NS(context=kw, headers={}),
    )
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(
                    body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes],
                    type_ignores=[],
                )
            ),
            "settings policies",
            "exec",
        ),
        ns,
    )
    return ns


def request(role="admin", active=True, membership=True):
    cls = next(
        n
        for n in ast.parse((ROOT / "app/models/identity/user.py").read_text()).body
        if isinstance(n, ast.ClassDef) and n.name == "User"
    )
    method = next(n for n in cls.body if getattr(n, "name", "") == "is_platform_admin")
    ns = {"PlatformRoleType": NS(ADMIN="admin")}
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(
                    body=[ast.ClassDef(name="Actor", bases=[], keywords=[], body=[method], decorator_list=[])],
                    type_ignores=[],
                )
            ),
            "real admin policy",
            "exec",
        ),
        ns,
    )
    user = ns["Actor"]()
    user.id, user.is_active, user.role = 101, active, NS(codename=role)
    return NS(
        state=NS(
            web_user=user,
            tenant_id=7 if membership else None,
            tenant=NS(id=7, is_active=True) if membership else None,
            memberships=[NS(tenant_id=7, user_id=101, channel="web", is_active=True)] if membership else [],
            web_perms=NS(can_manage_workspace=lambda tid: membership),
        ),
        query_params={"tab": "platforms"},
        session={},
    )


@pytest.fixture
def gateway(monkeypatch):
    ns = extracted()
    manager = NS(update_by_id=AsyncMock(return_value=NS(id=7)), create_platform=AsyncMock())

    # Awaitable query instead of a database-backed queryset.
    class Query:
        def order_by(self, *args):
            return self

        def __await__(self):
            async def result():
                return [NS(id=7)]

            return result().__await__()

    manager.filter = Mock(return_value=Query())
    platform = ModuleType("app.models.platform")
    platform.Platform = NS(objects=manager, id="id")
    types = ModuleType("app.types")

    class Kinds:
        def __iter__(self):
            return iter([NS(db_value="vk", label="ВКонтакте"), NS(db_value="telegram", label="Telegram")])

        def get_by_value(self, value):
            return next((k for k in self if k.db_value == value), None)

    types.PlatformType = Kinds()
    monkeypatch.setitem(sys.modules, platform.__name__, platform)
    monkeypatch.setitem(sys.modules, types.__name__, types)
    return ns, manager


@pytest.mark.parametrize("role", ["admin", "operator", "user", "owner", "unknown"])
@pytest.mark.parametrize("active", [True, False])
@pytest.mark.parametrize("membership", [True, False])
def test_visibility_uses_real_active_platform_role_not_workspace_ownership(role, active, membership):
    ns, req = extracted(), request(role, active, membership)
    assert ("platforms" in ns["_visible_tabs"](req, req.state.tenant_id)) == (active and role == "admin")


@pytest.mark.asyncio
async def test_selected_global_tab_loads_only_platforms_even_without_workspace(gateway):
    ns, manager = gateway
    response = await ns["settings_page"](request(membership=False))
    assert response.context["tab"] == "platforms" and response.context["settings_scope"] == "global"
    assert len(response.context["platforms"]) == 1
    assert response.headers["Cache-Control"] == "no-store"
    manager.filter.assert_called_once_with()


async def save(ns, req=None, **kw):
    args = dict(
        name="VK",
        base_url="https://api.example.invalid/v1/",
        platform_type="vk",
        platform_id=0,
        enabled="on",
        token="fake-csrf",
    )
    args.update(kw)
    return await ns["platform_save"](req or request(), **args)


@pytest.mark.asyncio
@pytest.mark.parametrize("role,active", [("user", True), ("operator", True), ("owner", True), ("admin", False)])
async def test_forged_writes_do_not_reach_manager(gateway, role, active):
    ns, manager = gateway
    assert (await save(ns, request(role, active))).status_code == 403
    manager.create_platform.assert_not_called()
    manager.update_by_id.assert_not_called()


@pytest.mark.asyncio
async def test_csrf_required_before_any_catalog_write(gateway):
    ns, manager = gateway
    ns["ensure_csrf"].return_value = False
    assert (await save(ns)).status_code == 403
    manager.create_platform.assert_not_called()
    manager.update_by_id.assert_not_called()


@pytest.mark.asyncio
async def test_create_and_update_use_existing_manager_without_deleting_or_overwriting_params(gateway):
    ns, manager = gateway
    assert (await save(ns)).status_code == 302
    assert manager.create_platform.call_args.kwargs["platform_type"].db_value == "vk"
    await save(ns, platform_id=7, platform_type="ignored", enabled="")
    manager.update_by_id.assert_awaited_once_with(
        7, name="VK", base_url="https://api.example.invalid/v1/", is_active=False
    )
    assert "params" not in manager.update_by_id.call_args.kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kw",
    [
        dict(name=" "),
        dict(name="x" * 51),
        dict(base_url="javascript:alert(1)"),
        dict(base_url="https://name:password@example.invalid"),
        dict(base_url="https://example.invalid:bad"),
        dict(base_url="https://[bad"),
        dict(platform_id=-1),
        dict(platform_type="fake"),
    ],
)
async def test_invalid_values_are_rejected_without_persistence(gateway, kw):
    ns, manager = gateway
    assert (await save(ns, **kw)).status_code == 302
    manager.create_platform.assert_not_called()
    manager.update_by_id.assert_not_called()
    assert ns["add_flash"].call_args.args[1] == "error"


@pytest.mark.asyncio
async def test_missing_and_duplicate_records_are_handled(gateway):
    from sqlalchemy.exc import IntegrityError

    ns, manager = gateway
    manager.update_by_id.return_value = None
    assert (await save(ns, platform_id=7)).status_code == 404
    manager.create_platform.side_effect = IntegrityError("insert", {}, Exception("private details"))
    assert (await save(ns)).status_code == 302
    assert "private details" not in str(ns["add_flash"].call_args)


@pytest.mark.parametrize("empty", [False, True])
def test_actual_template_is_a_settings_tab_with_shared_picker_and_safe_fields(empty):
    c = FIXTURES["context"]("owner-workspace")
    c.update(
        tab="platforms",
        visible_tabs=["account", "platforms"],
        platforms=(
            []
            if empty
            else [
                NS(
                    id=7,
                    name="<script>bad()</script>",
                    base_url="https://api.example.invalid/",
                    is_active=True,
                    platform_type=NS(label="ВКонтакте"),
                )
            ]
        ),
        platform_type_options=[("vk", "ВКонтакте", "")],
    )
    html = FIXTURES["environment"]().get_template("web/settings.html").render(**c)
    assert "/app/settings?tab=platforms" in html and "/admin/platform/list" not in html
    assert 'action="/app/settings/platforms"' in html and 'name="_csrf"' in html
    assert "data-popover-select" in html and "<select" not in html
    assert "<script>bad()" not in html
    assert 'name="params"' not in html and "/delete" not in html


@pytest.mark.asyncio
async def test_nonadmin_bookmark_never_queries_global_catalog(gateway):
    ns, manager = gateway
    response = await ns["settings_page"](request("operator"))
    assert response.context["tab"] == "account"
    assert "platforms" not in response.context
    manager.filter.assert_not_called()


@pytest.mark.parametrize("role,expected_redirect", [("admin", False), ("operator", True), ("user", True)])
def test_no_workspace_middleware_exception_is_only_for_platform_admin_post(role, expected_redirect):
    tree = ast.parse((ROOT / "app/web/middleware.py").read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.If) and "not memberships" in ast.unparse(n.test))
    values = dict(
        user=request(role, membership=False).state.web_user,
        memberships=[],
        path="/app/settings/platforms",
        request=NS(method="POST"),
        NO_WORKSPACE_PATHS=set(),
        PERSONAL_ACCOUNT_PATHS=set(),
    )
    assert (
        eval(compile(ast.Expression(node.test), "middleware onboarding predicate", "eval"), values) == expected_redirect
    )
