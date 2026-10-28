"""Actual Home/middleware/policy/render with storage-only doubles; no app bootstrap.

The source loader redirects imports per module, never sys.modules. JWT/session,
workspace selection, fresh grants, context scopes and Jinja HTML are real. Feed
helpers are bounded stubs: this suite does not claim ORM/DB/feed acceptance.
"""

import itertools
import socket
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from tests.source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]
SECRET = "p10-t08-offline-synthetic-session-key-only"
COMPONENTS = ("collecteditem", "aianalytics", "job")


@pytest.fixture
def home(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network access is forbidden in this source-only suite")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    before = {name for name in sys.modules if name == "app" or name.startswith("app.")}

    def load(name, path, imports=None):
        return load_isolated_source(name, ROOT / path, imports)

    settings = NS(SECRET_KEY=SECRET, ALGORITHM="HS256", DB_SCHEMA="synthetic_unused")
    enums = load("_p10_t08_db_enums", "app/utils/db_enums.py", {"app.core.config": NS(settings=settings)})
    types = load("_p10_t08_types", "app/types/enums/user_types.py", {"app.utils.db_enums": enums})
    tenant_context = load("_p10_t08_context", "app/core/tenant_context.py")
    catalog = load("_p10_t08_catalog", "app/core/access_catalog.py", {"app.types": types})
    permissions = load(
        "_p10_t08_permissions",
        "app/core/permissions.py",
        {"app.types": types, "app.core.access_catalog": catalog, "app.core.tenant_context": tenant_context},
    )
    web_perms = load("_p10_t08_web.perms", "app/web/perms.py", {"app.types": types, "app.core.access_catalog": catalog})
    nav = load("_p10_t08_web.nav", "app/web/nav.py", {"app.core.access_catalog": catalog})
    roles = load("_p10_t08_roles", "app/services/tenancy/member_roles.py")
    state = NS(
        calls=[],
        grants={},
        memberships=[],
        grant_reads=0,
        jobs=[NS(status="running", job_type="collect"), NS(status="pending", job_type="analyze")],
    )
    state.user = NS(
        id=7,
        username="synthetic",
        is_active=True,
        role=NS(codename="superuser", label="Superuser"),
        has_admin_access=lambda: True,
        has_perm_for=lambda *args: True,
    )

    class Tenant:
        PLANS = ("free",)
        PLAN_LIMITS = {"free": {"label": "Free"}}

        def __init__(self, tenant_id):
            self.id, self.name, self.timezone = tenant_id, f"Workspace {tenant_id}", "UTC"

    class Query:
        def __init__(self, model):
            self.model = model

        def filter(self, **kwargs):
            # Fail at query construction, not only when count/rows are awaited.
            assert tenant_context.current_tenant_id() in (11, 22)
            assert not tenant_context.is_bypass()
            assert kwargs["tenant_id"] == tenant_context.current_tenant_id()
            assert permissions.has_permission(permissions.get_current_user(), self.model, "view"), self.model
            state.calls.append((self.model, kwargs))
            return self

        async def count(self):
            return {"collecteditem": 71, "aianalytics": 83}.get(self.model, 1)

        def order_by(self, *args):
            return self

        async def limit(self, limit):
            assert self.model == "job" and limit == 3  # Home preview remains bounded.
            return state.jobs

    def model(name):
        return NS(objects=Query(name), created_at=NS(desc=lambda: "created_at DESC"), id=NS(desc=lambda: "id DESC"))

    models = {name: model(name) for name in (*COMPONENTS, "source", "agenttask")}
    deps = load(
        "_p10_t08_web.deps",
        "app/web/deps.py",
        {
            "app.models": NS(Tenant=Tenant),
            "app.types": types,
            "_p10_t08_web.nav": nav,
            "app.services.tenancy": NS(member_roles=roles),
        },
    )

    class Users:
        def prefetch_related(self, *args):
            assert args == ("role.permissions",)
            return self

        async def get(self, **kwargs):
            assert kwargs == {"id": 7}
            return state.user

    class Members:
        async def web_memberships(self, user_id):
            assert user_id == 7
            return [m for m in state.memberships if m.is_active and m.channel == "web"]

        async def member_grants(self, member):
            state.grant_reads += 1
            return state.grants.get(member.tenant_id, frozenset())

    async def get_tenant(**kwargs):
        return Tenant(kwargs["id"])

    middleware = load(
        "_p10_t08_web.middleware",
        "app/web/middleware.py",
        {
            "app.core.config": NS(settings=settings),
            "app.core.permissions": permissions,
            "app.core.tenant_context": tenant_context,
            "app.models": NS(User=NS(objects=Users())),
            "app.models.managers.tenant_manager": NS(TenantUserManager=Members, tenants=NS(get=get_tenant)),
            "app.services.tenancy": NS(member_roles=roles),
            "_p10_t08_web.nav": nav,
            "_p10_t08_web.perms": web_perms,
            "_p10_t08_web.deps": deps,
        },
    )
    dashboard = load(
        "_p10_t08_web.dashboard",
        "app/web/dashboard.py",
        {
            "_p10_t08_web.deps": deps,
            "app.models.agent_task": NS(AgentTask=models["agenttask"]),
            "app.models.ai_analytics": NS(AIAnalytics=models["aianalytics"]),
            "app.models.collection.source": NS(Source=models["source"]),
            "app.models.collected_item": NS(CollectedItem=models["collecteditem"]),
            "app.models.job": NS(Job=models["job"]),
        },
    )
    for helper in ("_recent_results", "_recent_digests", "_recent_tasks"):
        monkeypatch.setattr(dashboard, helper, AsyncMock(return_value=[]))

    def member(tenant_id=11, role="custom", owner=False):
        return NS(
            tenant_id=tenant_id,
            user_id=7,
            channel="web",
            is_active=True,
            is_owner=owner,
            role_id=1,
            role=NS(codename=role),
            ai_chat_enabled=False,
        )

    state.memberships = [member()]
    state.member, state.catalog, state.types, state.dashboard = member, catalog, types, dashboard
    state.context, state.permissions = tenant_context, permissions
    state.set_grants = lambda names, tenant=11: state.grants.update(
        {tenant: frozenset((name, types.ActionType.VIEW) for name in names)}
    )
    app = FastAPI()
    app.state.csrf_manager = NS(generate_token=lambda: "synthetic-csrf")
    app.mount("/static", StaticFiles(directory=ROOT / "app/static"), name="static")
    app.include_router(dashboard.router, prefix="/app")

    @app.get("/test-session")
    async def session(request: Request, tenant: int = 11):
        request.session["token"] = jwt.encode({"sub": "7", "type": "access"}, SECRET, algorithm="HS256")
        request.session["app_tenant_id"] = tenant
        return PlainTextResponse("ok")

    @app.get("/app/jobs")
    async def jobs():
        return PlainTextResponse("authorized section reached")

    app.add_middleware(middleware.TenantUIMiddleware)
    app.add_middleware(tenant_context.PlatformScopeMiddleware)
    app.add_middleware(SessionMiddleware, secret_key=SECRET)
    with TestClient(app, follow_redirects=False) as client:
        state.client = client
        client.get("/test-session")
        yield state
    assert {name for name in sys.modules if name == "app" or name.startswith("app.")} == before
    assert tenant_context.current_tenant_id() is None
    assert not tenant_context.is_bypass()
    assert permissions.get_current_user() is None


def assert_components(home, allowed, response):
    assert response.status_code == 200
    html = response.text
    expected = set(allowed) & set(COMPONENTS)
    assert {name for name, _ in home.calls if name in COMPONENTS} == expected
    assert ("data-home-pipeline" in html) == bool(expected)
    for model, label in (
        ("collecteditem", "сохранено материалов"),
        ("aianalytics", "сохранено анализов"),
        ("job", "выполняются в предпросмотре"),
    ):
        assert (label in html) == (model in expected)
    assert ('href="/app/jobs"' in html) == ("job" in expected)
    assert ("Сбор данных выполняется…" in html) == ("job" in expected)
    assert ("ожидают в предпросмотре" in html) == ("job" in expected)
    assert ("71" in html) == ("collecteditem" in expected)
    assert ("83" in html) == ("aianalytics" in expected)
    if "aianalytics" not in allowed:
        home.dashboard._recent_results.assert_not_awaited()
    if "agenttask" not in allowed:
        home.dashboard._recent_tasks.assert_not_awaited()


@pytest.mark.parametrize("bits", list(itertools.product((False, True), repeat=3)))
@pytest.mark.parametrize("task_view", [False, True])
def test_independent_pipeline_components(home, bits, task_view):
    allowed = {name for name, enabled in zip(COMPONENTS, bits) if enabled}
    if task_view:
        allowed.add("agenttask")
    home.set_grants(allowed)
    assert_components(home, allowed, home.client.get("/app/"))


@pytest.mark.parametrize("role", ["owner", "admin", "manager", "analyst", "viewer"])
def test_actual_workspace_presets_do_not_inherit_platform_rights(home, role):
    home.memberships = [home.member(role=role, owner=role == "owner")]
    home.grants[11] = home.catalog.workspace_grants(role)
    allowed = {name for name, action in home.grants[11] if action == home.types.ActionType.VIEW}
    assert_components(home, allowed, home.client.get("/app/"))


@pytest.mark.parametrize("revocation", ["grants", "role", "membership", "user"])
def test_existing_session_rechecks_revocation(home, revocation):
    home.set_grants(COMPONENTS)
    assert_components(home, COMPONENTS, home.client.get("/app/"))
    home.calls.clear()
    if revocation == "grants":
        home.set_grants([])
    elif revocation == "role":
        home.memberships[0].role = None
        home.memberships[0].role_id = None
    elif revocation == "membership":
        home.memberships[0].is_active = False
    else:
        home.user.is_active = False
    response = home.client.get("/app/")
    if revocation in {"grants", "role"}:
        assert response.status_code == 200
        assert "data-home-pipeline" not in response.text
        assert 'href="/app/jobs"' not in response.text
        assert home.grant_reads == 2
    else:
        assert response.status_code == 302
        assert response.headers["location"].startswith("/app/invite" if revocation == "membership" else "/app/login")
    assert not home.calls
    assert home.client.get("/app/jobs").status_code in (302, 403)


def test_workspace_selection_uses_only_active_grants_and_scope(home):
    home.memberships.append(home.member(22))
    home.set_grants(COMPONENTS, tenant=11)
    home.set_grants(["aianalytics"], tenant=22)
    home.client.get("/test-session?tenant=22")
    assert_components(home, {"aianalytics"}, home.client.get("/app/"))
    assert all(kwargs["tenant_id"] == 22 for _, kwargs in home.calls)
    home.calls.clear()
    home.memberships[1].is_active = False
    assert_components(home, COMPONENTS, home.client.get("/app/"))
    assert all(kwargs["tenant_id"] == 11 for _, kwargs in home.calls)


@pytest.mark.parametrize("granted", [False, True])
def test_queue_link_agrees_with_actual_middleware_section_gate(home, granted):
    home.set_grants(["job"] if granted else ["aianalytics"])
    response = home.client.get("/app/")
    assert_components(home, {"job"} if granted else {"aianalytics"}, response)
    response = home.client.get("/app/jobs")
    assert response.status_code == (200 if granted else 403)


@pytest.mark.parametrize("superuser", [False, True])
def test_pipeline_without_selected_workspace_never_reads(home, superuser):
    import asyncio

    result = asyncio.run(home.dashboard._pipeline(None, superuser, {"analytics": True, "jobs": True}))
    assert result == ({"scope_all": True} if superuser else None)
    assert not home.calls


@pytest.mark.parametrize("platform_role", ["user", "operator", "admin", "superuser"])
def test_platform_role_alone_never_grants_pipeline_or_queue(home, platform_role):
    home.user.role.codename = platform_role
    home.set_grants([])
    assert_components(home, set(), home.client.get("/app/"))
    assert home.client.get("/app/jobs").status_code == 403


@pytest.mark.parametrize(
    "rows, text",
    [
        ([], "Нет ожидающих или выполняющихся заданий."),
        (
            [NS(status="pending", job_type="collect")],
            "В предпросмотре — ожидающие задания. Время запуска зависит от расписания.",
        ),
    ],
)
def test_job_empty_and_pending_states_require_job_view(home, rows, text):
    home.jobs = rows
    home.set_grants(["job"])
    response = home.client.get("/app/")
    assert response.status_code == 200 and text in response.text
    home.set_grants(["aianalytics"])
    home.calls.clear()
    response = home.client.get("/app/")
    assert response.status_code == 200
    assert text not in response.text
    assert 'href="/app/jobs"' not in response.text
    assert {name for name, _ in home.calls} == {"aianalytics"}


def test_wrong_action_is_not_view_permission(home):
    home.grants[11] = frozenset((name, home.types.ActionType.EXPORT) for name in COMPONENTS)
    assert_components(home, set(), home.client.get("/app/"))
    assert home.client.get("/app/jobs").status_code == 403
