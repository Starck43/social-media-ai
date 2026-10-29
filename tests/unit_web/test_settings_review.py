"""Owner review regressions using strict templates and extracted server policies only."""

import ast
import runpy
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.responses import RedirectResponse

ROOT = Path(__file__).resolve().parents[2]
fixtures = runpy.run_path(str(ROOT / "tests/shared/settings_layout_fixtures.py"))


def extract(path, name):
    node = next(n for n in ast.parse((ROOT / path).read_text()).body if getattr(n, "name", "") == name)
    if isinstance(node, ast.ClassDef):
        node = next(n for n in node.body if getattr(n, "name", "") == "_change_team")
    node.decorator_list = []
    return compile(
        ast.fix_missing_locations(
            ast.Module(
                body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node],
                type_ignores=[],
            )
        ),
        str(path),
        "exec",
    )


def fake_module(monkeypatch, name, **values):
    module = ModuleType(name)
    module.__dict__.update(values)
    monkeypatch.setitem(sys.modules, name, module)


def test_owner_and_self_never_render_access_controls():
    for self_id in [101, 102]:
        c = fixtures["context"]("owner-team")
        c["user"].id = self_id
        html = fixtures["environment"]().get_template("web/settings.html").render(**c)
        assert "/app/settings/members/1/role" not in html
        assert f"/app/settings/members/{1 if self_id == 101 else 2}/role" not in html
        assert "/app/settings/members/3/role" in html


def test_plans_are_visible_without_disclosure():
    html = fixtures["environment"]().get_template("web/settings.html").render(**fixtures["context"]("owner-workspace"))
    assert "settings-plans-heading" in html and "Тарифные планы" in html
    assert "<table" in html and "settings-plan-details" not in html


@pytest.mark.parametrize(
    "role,active,expected",
    [(r, a, a and r in ("operator", "admin")) for r in ("user", "operator", "admin") for a in (True, False)],
)
def test_admin_link_matches_real_platform_policy(role, active, expected):
    cls = next(
        n
        for n in ast.parse((ROOT / "app/models/identity/user.py").read_text()).body
        if isinstance(n, ast.ClassDef) and n.name == "User"
    )
    method = next(n for n in cls.body if getattr(n, "name", "") == "has_admin_access")
    ns = dict(PlatformRoleType=NS(OPERATOR="operator", ADMIN="admin"))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), "admin policy", "exec"), ns)
    user = NS(is_active=active, role=NS(codename=role))
    c = fixtures["context"]("owner-workspace")
    c["user"].has_admin_access = lambda: ns["has_admin_access"](user)
    html = fixtures["environment"]().get_template("web/settings.html").render(**c)
    assert ('class="settings-admin"' in html) is expected


def test_every_settings_select_uses_the_same_macro_without_invalid_empty_choices():
    env = fixtures["environment"]()
    c = fixtures["context"]("owner-workspace")
    c["is_superuser"] = True
    pages = [env.get_template("web/settings.html").render(**c)]
    c = fixtures["context"]("connections")
    c["connections"][0]["manual_kinds"].append(("session", "Session"))
    pages.append(env.get_template("web/settings.html").render(**c))
    pages.append(env.get_template("web/settings.html").render(**fixtures["context"]("owner-team")))
    pages.append(env.get_template("web/_invite_dialog.html").render(**fixtures["invite_context"]()))
    for html in pages:
        assert "<select" not in html
        assert "data-popover-select" in html
        assert 'data-popover-option=""' not in html


class Expression:
    def __eq__(self, other):
        return self

    def is_(self, other):
        return self

    def in_(self, other):
        return self


class Scalars(list):
    def all(self):
        return self


class Query:
    def __init__(self, model):
        self.model = model

    def where(self, *args):
        return self

    def with_for_update(self):
        return self

    def options(self, *args):
        return self


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authority,self_target", [("owner", True), ("admin", True), ("owner", False), ("admin", False)]
)
async def test_manager_denies_self_role_even_with_another_owner(monkeypatch, authority, self_target):
    class Model:
        id = tenant_id = is_active = role = Expression()

    for name in ["app", "app.models", "app.models.tenancy"]:
        fake_module(monkeypatch, name, __path__=[])
    fake_module(monkeypatch, "app.models.tenancy.workspace_role", WorkspaceRole=Model)
    fake_module(monkeypatch, "app.models.tenancy.tenant", Tenant=Model)
    fake_module(monkeypatch, "app.models.identity.user", User=Model)
    actor = NS(id=101)
    caller = NS(id=1, user_id=101, channel="web", is_active=True, role=NS(codename=authority), role_id=10)
    target = (
        caller
        if self_target
        else NS(id=2, user_id=102, channel="web", is_active=True, role=NS(codename="viewer"), role_id=20)
    )
    peer = NS(id=3, user_id=103, channel="web", is_active=True, role=NS(codename="owner"), role_id=30)
    values = iter([NS(id=7), actor, [caller, target, peer], NS(id=40, codename="analyst"), [103]])
    queries = []

    async def execute(q):
        queries.append(q)
        value = next(values)
        return NS(scalar_one_or_none=lambda: value, scalars=lambda: Scalars(value))

    @asynccontextmanager
    async def session():
        yield NS(execute=execute)

    ns = dict(
        __name__="app.models.managers.tenant_manager",
        __package__="app.models.managers",
        select=Query,
        selectinload=lambda x: x,
        get_current_user=lambda: actor,
        current_tenant_id=lambda: 7,
        is_bypass=lambda: False,
        WEB_CHANNEL="web",
        logger=NS(info=lambda *a, **k: None),
    )
    exec(extract("app/models/managers/tenant_manager.py", "TenantUserManager"), ns)
    original = target.role_id
    assert await ns["_change_team"](
        NS(model=Model, _get_owned_session=session), tenant_id=7, membership_id=target.id, role_id=40
    ) is (not self_target)
    assert target.role_id == (original if self_target else 40)
    assert len(queries) == (3 if self_target else 4)


@pytest.mark.asyncio
async def test_direct_self_role_post_stops_before_role_resolution_or_write(monkeypatch):
    for name in ["app", "app.models", "app.models.managers"]:
        fake_module(monkeypatch, name, __path__=[])
    fake_module(monkeypatch, "app.models", WorkspaceRole=NS())
    users = NS(get=AsyncMock(return_value=NS(id=4, user_id=101)), change_member_role=AsyncMock())
    fake_module(monkeypatch, "app.models.managers.tenant_manager", tenant_users=users)
    resolve = Mock(side_effect=AssertionError("Role resolution must not run"))
    ns = dict(
        Form=lambda *a, **k: None,
        _guard_workspace_settings=lambda *a: None,
        ensure_csrf=lambda *a: True,
        add_flash=Mock(),
        BACK="/app/settings",
        RedirectResponse=RedirectResponse,
        member_roles=NS(resolve=resolve),
    )
    exec(extract("app/web/settings.py", "membership_role"), ns)
    response = await ns["membership_role"](NS(state=NS(tenant_id=7, web_user=NS(id=101))), 4, "viewer", "fixture")
    assert response.status_code == 302
    users.get.assert_awaited_once_with(id=4, tenant_id=7)
    users.change_member_role.assert_not_awaited()
    resolve.assert_not_called()
