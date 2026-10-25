"""Isolated access contracts: AST handlers, pure policy, fake ORM and offline SQL only.

No application import, engine, root conftest, ASGI startup or provider call.
These checks are not real role/tenant/database acceptance.
"""

from __future__ import annotations

import ast
import runpy
import sys
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, StrictUndefined, select_autoescape
from sqlalchemy import Boolean, Column, ForeignKey, Integer, MetaData, String, Table, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import registry, relationship, selectinload
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

ROOT = Path(__file__).resolve().parents[2]


def load_nodes(path, names, *, methods=None, namespace=None):
    nodes = [n for n in ast.parse((ROOT / path).read_text()).body if getattr(n, "name", "") in names]
    for n in nodes:
        if isinstance(n, ast.ClassDef):
            n.bases = []
            n.decorator_list = []
            if methods:
                n.body = [m for m in n.body if getattr(m, "name", "") in methods]
        for m in ast.walk(n):
            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                m.decorator_list = []
                m.args.defaults = [ast.Constant(None) for _ in m.args.defaults]
    ns = namespace or {}
    ns.setdefault("__name__", "app.models.managers.tenant_manager")
    ns.setdefault("__package__", "app.models.managers")
    tree = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(tree), path, "exec"), ns)
    return ns


def module(monkeypatch, name, **values):
    fake = ModuleType(name)
    fake.__dict__.update(values)
    monkeypatch.setitem(sys.modules, name, fake)


# This module is stdlib-only: no application package initialization.
policy = runpy.run_path(str(ROOT / "app/services/tenancy/web_access.py"))["owns_web_workspace"]
nav = runpy.run_path(str(ROOT / "app/web/nav.py"))
# Read the middleware allowlist itself so the isolated loader sees the same paths.
PERSONAL_ACCOUNT_PATHS = next(
    eval(compile(ast.Expression(node.value), "web middleware constant", "eval"), {})
    for node in ast.parse((ROOT / "app/web/middleware.py").read_text()).body
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == "PERSONAL_ACCOUNT_PATHS" for target in node.targets)
)


def user(**kw):
    return NS(**dict(id=101, is_active=True, is_platform_admin=False, has_admin_access=lambda: False, **kw))


def member(**kw):
    values = dict(tenant_id=7, user_id=101, channel="web", is_active=True, is_owner=True)
    values.update(kw)
    return NS(**values)


@pytest.mark.parametrize(
    "changes",
    [dict(is_owner=False), dict(is_active=False), dict(channel="telegram"), dict(user_id=102), dict(tenant_id=8)],
)
def test_chat_requires_active_bound_owner_not_guest(changes):
    assert not policy(user(), [member(**changes)], 7)


@pytest.mark.parametrize("tid", [None, 0, -1, True, "7", 8])
def test_chat_denies_invalid_or_foreign_selection(tid):
    assert not policy(user(), [member()], tid)


@pytest.mark.parametrize("active", [True, False])
@pytest.mark.parametrize("owner", [True, False])
@pytest.mark.parametrize("operator", [True, False])
def test_owner_guest_platform_admin_matrix(active, owner, operator):
    caller = NS(id=101, is_active=active, is_platform_admin=operator)
    assert policy(caller, [member(is_owner=owner)], 7) is (active and owner)


def test_workspace_switch_and_revocation_change_chat_authority():
    caller = user()
    memberships = [member(), member(tenant_id=8, is_owner=False)]
    assert policy(caller, memberships, 7)
    assert not policy(caller, memberships, 8)
    memberships[0].is_active = False
    assert not policy(caller, memberships, 7)
    assert not policy(None, memberships, 7)
    assert not policy(caller, [], 7)


@pytest.mark.parametrize("path", ["/app/chat", "/app/chat/", "/app/chat/message/3/delete"])
def test_chat_nav_and_direct_section_share_gate(path):
    item = nav["section_of"](path)
    assert item.key == "chat"
    assert not nav["allows"](item, NS(can_open_chat=False))
    assert nav["allows"](item, NS(can_open_chat=True))
    assert nav["section_of"]("/app/chat-other") is None


def chat_namespace():
    return load_nodes(
        "app/web/chat.py",
        {"_chat_denied", "chat_page", "chat_send", "chat_message_delete"},
        namespace=dict(
            JSONResponse=JSONResponse,
            RedirectResponse=RedirectResponse,
            ensure_csrf=lambda *a: True,
            render_section_denied=lambda *a: HTMLResponse("Denied", status_code=403),
            section_of=nav["section_of"],
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["chat_page", "chat_send", "chat_message_delete"])
@pytest.mark.parametrize("ajax", [True, False])
async def test_guest_routes_refuse_before_runtime_import_or_query(endpoint, ajax):
    ns = chat_namespace()
    req = NS(
        state=NS(web_perms=NS(can_open_chat=False)), headers={"accept": "application/json" if ajax else "text/html"}
    )
    args = dict(request=req)
    if endpoint == "chat_message_delete":
        args["message_id"] = 3
    response = await ns[endpoint](**args)
    assert response.status_code == 403
    assert ("error" in response.body.decode()) is ajax
    # Any accidental runtime/model import would fail: app is never loaded here.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identity,admitted",
    [
        (NS(user_id=101, is_owner=True, ai_chat_enabled=True, user=user(), member_grants=frozenset()), True),
        (NS(user_id=101, is_owner=False, ai_chat_enabled=True, user=user(), member_grants=frozenset()), True),
        (NS(user_id=101, is_owner=False, ai_chat_enabled=False, user=user(), member_grants=frozenset()), False),
        (None, False),
    ],
)
async def test_runtime_requires_fresh_ai_chat_admission_before_turn(identity, admitted):
    ns = load_nodes(
        "app/agent/runtime.py",
        {"_handle_in_tenant"},
        namespace=dict(
            WEB_CHANNEL="web",
            current_tenant_id=lambda: 7,
            resolve_runtime_identity=AsyncMock(return_value=identity),
            memory_actor_scope=lambda *args: nullcontext(),
            permission_scope=lambda *a, **kw: nullcontext(),
            _handle_authorized_turn=AsyncMock(return_value="mock reply"),
        ),
    )
    inbound = NS(channel="web", chat_id="101", user_id="101")
    resolution = NS(tenant_id=7, channel="web", chat_id="101", user_id="101")
    if admitted:
        assert await ns["_handle_in_tenant"](inbound, resolution) == "mock reply"
        ns["_handle_authorized_turn"].assert_awaited_once()
    else:
        with pytest.raises(PermissionError):
            await ns["_handle_in_tenant"](inbound, resolution)
        ns["_handle_authorized_turn"].assert_not_awaited()


@pytest.mark.asyncio
async def test_messenger_member_requires_ai_chat_grant():
    ns = load_nodes(
        "app/agent/runtime.py",
        {"_handle_in_tenant"},
        namespace=dict(
            WEB_CHANNEL="web",
            current_tenant_id=lambda: 7,
            resolve_runtime_identity=AsyncMock(
                return_value=NS(user_id=101, is_owner=False, ai_chat_enabled=True, user=user(), member_grants=frozenset())
            ),
            memory_actor_scope=lambda *args: nullcontext(),
            permission_scope=lambda *a, **kw: nullcontext(),
            _handle_authorized_turn=AsyncMock(return_value="mock"),
        ),
    )
    data = NS(channel="telegram", chat_id="101", user_id="101")
    assert await ns["_handle_in_tenant"](data, NS(tenant_id=7, **data.__dict__)) == "mock"
    ns["resolve_runtime_identity"].return_value.ai_chat_enabled = False
    assert "AI-чату" in await ns["_handle_in_tenant"](data, NS(tenant_id=7, **data.__dict__))


@pytest.mark.parametrize("active", [True, False])
@pytest.mark.parametrize("codename,expected", [("admin", True), ("operator", True), ("user", False)])
def test_admin_entry_requires_active_platform_operator_or_admin(active, codename, expected):
    enum = NS(ADMIN="admin", OPERATOR="operator")
    cls = load_nodes(
        "app/models/identity/user.py",
        {"User"},
        methods={"has_admin_access"},
        namespace={"PlatformRoleType": enum},
    )["User"]
    obj = cls()
    obj.is_active = active
    obj.role = NS(codename=codename)
    assert obj.has_admin_access() is (active and expected)


@pytest.mark.asyncio
async def test_admin_auth_binds_verified_token_identity_not_stale_session(monkeypatch):
    loaded = NS(id=101, username="verified", is_active=True, has_admin_access=lambda: True)
    get = AsyncMock(return_value=loaded)
    auth = AsyncMock(return_value=NS(id=101, is_active=True))
    cls = load_nodes(
        "app/admin/auth.py",
        {"AdminAuthBackend"},
        methods={"authenticate"},
        namespace=dict(
            User=NS(objects=NS(prefetch_related=lambda *a: NS(get=get))),
            get_authenticated_user=auth,
            HTTPException=RuntimeError,
        ),
    )["AdminAuthBackend"]
    req = NS(session={"token": "mock-token", "user_id": "999", "app_tenant_id": 7}, state=NS())
    assert await cls().authenticate(req)
    get.assert_awaited_once_with(id=101)
    assert req.state.admin_user is loaded
    assert req.session["user_id"] == "101"
    assert req.session["app_tenant_id"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "mock-token"])
async def test_external_admin_guard_requires_token_not_user_id(monkeypatch, token):
    loaded = NS(id=101, is_active=True)
    get = AsyncMock(return_value=loaded)
    authenticate = AsyncMock(return_value=NS(id=101, is_active=True))
    module(monkeypatch, "app.services.user.auth", get_authenticated_user=authenticate)
    cls = load_nodes(
        "app/admin/authorization.py",
        {"AdminAuthorizationBackend"},
        methods={"resolve_user"},
        namespace=dict(
            _MISSING=object(),
            User=NS(objects=NS(prefetch_related=lambda *a: NS(get=get))),
            logger=NS(warning=lambda *a, **k: None),
        ),
    )["AdminAuthorizationBackend"]
    req = NS(session={"user_id": "999", **({"token": token} if token else {})}, state=NS())
    result = await cls().resolve_user(req)
    assert result is (loaded if token else None)
    if token:
        get.assert_awaited_once_with(id=101)
    else:
        get.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("grants", [set(), {"view"}, {"create"}, {"update"}, {"delete"}, {"export"}, {"configure"}])
async def test_admin_model_action_grants_remain_separate(grants):
    cls = load_nodes(
        "app/admin/authorization.py",
        {"AdminAuthorizationBackend"},
        methods={"get_grants"},
        namespace=dict(
            WILDCARD="*",
            Action=NS(LIST="list", DETAILS="details"),
            ActionType=NS(VIEW="view"),
            BUILTIN_ACTION_TYPES={
                "list": "view",
                "details": "view",
                "create": "create",
                "edit": "update",
                "delete": "delete",
                "export": "export",
                "import": "create",
            },
            custom_action=lambda key: "action:" + key,
        ),
    )["AdminAuthorizationBackend"]
    backend = cls()
    backend.resolve_user = AsyncMock(
        return_value=NS(
            has_admin_access=lambda: True,
            is_platform_admin=False,
            model_permissions=lambda name: grants if name == "source" else set(),
        )
    )
    backend._catalog = lambda request: ({"source": "source", "user": "user"}, {"source": {"probe": "configure"}})
    result = await backend.get_grants(NS())
    assert not any(model == "user" for model, verb in result)
    assert (("source", "delete") in result) is ("delete" in grants)
    assert (("source", "create") in result) is ("create" in grants)
    assert (("source", "edit") in result) is ("update" in grants)
    assert (("source", "action:probe") in result) is ("configure" in grants)
    assert (("source", "list") in result) is bool(grants)


@pytest.mark.asyncio
@pytest.mark.parametrize("manage,csrf", [(False, True), (True, False), (True, True)])
async def test_member_disable_checks_capability_and_csrf_before_manager(monkeypatch, manage, csrf):
    disable = AsyncMock(return_value=True)
    module(monkeypatch, "app.models.managers.tenant_manager", tenant_users=NS(disable_web_member=disable))
    ns = load_nodes(
        "app/web/settings.py",
        {"membership_disable"},
        namespace=dict(
            BACK="/app/settings",
            RedirectResponse=RedirectResponse,
            _guard_workspace_settings=lambda *a: None if manage else HTMLResponse("Denied", status_code=403),
            ensure_csrf=lambda *a: csrf,
            add_flash=lambda *a: None,
        ),
    )
    response = await ns["membership_disable"](NS(state=NS(tenant_id=7)), 33, "mock-csrf")
    if manage and csrf:
        disable.assert_awaited_once_with(tenant_id=7, membership_id=33)
    else:
        disable.assert_not_awaited()
    assert response.status_code == (403 if not manage else 302)


@pytest.mark.asyncio
async def test_team_change_locks_workspace_and_updates_only_authorized_target(monkeypatch):
    metadata = MetaData(schema="offline_fixture")
    tenant_table = Table(
        "tenants",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("is_active", Boolean),
    )
    user_table = Table(
        "users",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("is_active", Boolean),
    )
    role_table = Table(
        "workspace_roles",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("codename", String),
    )
    member_table = Table(
        "tenant_users",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("tenant_id", Integer),
        Column("channel", String),
        Column("user_id", Integer),
        Column("role_id", ForeignKey("offline_fixture.workspace_roles.id")),
        Column("is_active", Boolean),
    )
    mappings = registry()

    @mappings.mapped
    class Tenant:
        __table__ = tenant_table

    @mappings.mapped
    class User:
        __table__ = user_table

    @mappings.mapped
    class WorkspaceRole:
        __table__ = role_table

    @mappings.mapped
    class Member:
        __table__ = member_table
        role = relationship(WorkspaceRole)

    actor = NS(id=101)
    caller_role = WorkspaceRole(id=1, codename="owner")
    target_role = WorkspaceRole(id=2, codename="viewer")
    caller = Member(id=11, tenant_id=7, channel="web", user_id=101, role_id=1, is_active=True, role=caller_role)
    target = Member(id=33, tenant_id=7, channel="web", user_id=202, role_id=2, is_active=True, role=target_role)
    workspace = Tenant(id=7, is_active=True)
    fresh_actor = User(id=101, is_active=True)

    module(monkeypatch, "app", __path__=[])
    module(monkeypatch, "app.models", __path__=[])
    module(monkeypatch, "app.models.managers", __path__=[])
    module(monkeypatch, "app.models.tenancy", __path__=[])
    module(monkeypatch, "app.models.tenancy.workspace_role", WorkspaceRole=WorkspaceRole)
    module(monkeypatch, "app.models.tenant", Tenant=Tenant)
    module(monkeypatch, "app.models.user", User=User)
    cls = load_nodes(
        "app/models/managers/tenant_manager.py",
        {"TenantUserManager"},
        methods={"_change_team", "disable_web_member"},
        namespace=dict(
            select=select,
            selectinload=selectinload,
            WEB_CHANNEL="web",
            get_current_user=lambda: actor,
            current_tenant_id=lambda: 7,
            is_bypass=lambda: False,
            logger=NS(info=lambda *a, **k: None),
        ),
    )["TenantUserManager"]
    statements = []

    async def execute(statement):
        statements.append(statement)
        entity = statement.column_descriptions[0]["entity"]
        row = {Tenant: workspace, User: fresh_actor}.get(entity)
        return NS(
            scalar_one_or_none=lambda: row,
            scalars=lambda: [caller, target],
        )

    @asynccontextmanager
    async def session():
        yield NS(execute=execute)

    manager = cls()
    manager.model, manager._get_owned_session = Member, session
    assert await manager.disable_web_member(tenant_id=7, membership_id=33)
    sql = [
        str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        for statement in statements
    ]
    assert len(sql) == 3
    assert all("FOR UPDATE" in statement for statement in sql[:2])
    assert "tenant_users.tenant_id = 7" in sql[2]
    assert target.is_active is False
    assert caller.is_active is True


def test_shell_context_labels_admin_entry_and_member_disable_markup():
    from test_assistant_shell import context, environment

    for owner in [True, False]:
        data = context(manager=owner)
        data["user"].role = None
        data["user"].has_admin_access = lambda: True
        data["workspaces"] = [
            dict(
                tenant=NS(id=7, name="<script>other</script>"),
                active=True,
                is_owner=owner,
                role_label="Owner" if owner else "Viewer",
                role="owner" if owner else "viewer",
            )
        ]
        html = environment().get_template("shell-fixture.html").render(**data)
        assert ("Своё пространство" if owner else "Доступ предоставлен") in html
        assert "data-workspace-context" in html
        assert 'href="/admin/"' in html
        assert "&lt;script&gt;other&lt;/script&gt;" in html
    template = (ROOT / "app/web/templates/web/settings.html").read_text()
    assert 'data-confirm="Отключить доступ' in template
    assert "/members/{{ m.id }}/disable" in template
    assert "m_key != 'owner'" in template


def test_chat_has_no_additional_unprotected_attachment_route():
    tree = ast.parse((ROOT / "app/web/chat.py").read_text())
    routes = [n.name for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.decorator_list]
    assert set(routes) == {"chat_page", "chat_send", "chat_message_delete"}
    runtime = (ROOT / "app/agent/runtime.py").read_text()
    assert "Reload membership instead of trusting" not in runtime  # No changes to messenger turn.


@pytest.mark.parametrize(
    "owner,ai_chat_enabled,expected", [(True, False, True), (False, True, True), (False, False, False)]
)
def test_webperms_chat_requires_owner_or_explicit_membership_grant(owner, ai_chat_enabled, expected):
    cls = load_nodes(
        "app/web/perms.py",
        {"WebPerms"},
        methods={"can_open_chat", "_membership"},
        namespace={"owns_web_workspace": policy},
    )["WebPerms"]
    perms = cls()
    perms._user = NS(id=101, is_active=True, is_platform_admin=True)
    perms._tenant_id = 7
    perms._memberships = [
        member(is_owner=owner, role=NS(codename="owner" if owner else "viewer"), ai_chat_enabled=ai_chat_enabled)
    ]
    assert perms.can_open_chat() is expected
    perms._memberships[0].is_active = False
    assert not perms.can_open_chat()


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_middleware_denies_direct_guest_chat_reads(method):
    cls = load_nodes(
        "app/web/middleware.py",
        {"TenantUIMiddleware"},
        methods={"_denied_section"},
        namespace={
            "section_of": nav["section_of"],
            "allows": nav["allows"],
            "PERSONAL_ACCOUNT_PATHS": PERSONAL_ACCOUNT_PATHS,
        },
    )["TenantUIMiddleware"]
    request = NS(method=method, state=NS(web_user=user(), web_perms=NS(can_open_chat=False)))
    assert cls._denied_section(request, "/app/chat").key == "chat"
    request.state.web_perms.can_open_chat = True
    assert cls._denied_section(request, "/app/chat") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("inactive", [True, False])
async def test_admin_token_resolution_denies_inactive_missing_account(monkeypatch, inactive):
    authenticated = NS(id=101, is_active=True)
    module(monkeypatch, "app.services.user.auth", get_authenticated_user=AsyncMock(return_value=authenticated))
    cls = load_nodes(
        "app/admin/authorization.py",
        {"AdminAuthorizationBackend"},
        methods={"resolve_user"},
        namespace=dict(
            _MISSING=object(),
            User=NS(
                objects=NS(
                    prefetch_related=lambda *a: NS(
                        get=AsyncMock(return_value=NS(is_active=False) if inactive else None)
                    )
                )
            ),
            logger=NS(warning=lambda *a, **k: None),
        ),
    )["AdminAuthorizationBackend"]
    assert await cls().resolve_user(NS(state=NS(), session={"token": "mock-token", "user_id": "999"})) is None


def test_admin_index_has_an_explanation_and_no_implicit_model_data():
    env = Environment(
        loader=ChoiceLoader(
            [
                DictLoader(
                    {
                        "sqladmin/layout.html": "<html>{% block content_header %}{% endblock %}{% block content %}{% endblock %}</html>"
                    }
                ),
                FileSystemLoader(ROOT / "app/templates"),
            ]
        ),
        undefined=StrictUndefined,
        autoescape=select_autoescape(),
    )
    html = env.get_template("sqladmin/index.html").render()
    assert "data-admin-entry" in html
    assert "Если список моделей пуст" in html
    assert "/app/" in html
    assert "<table" not in html


@pytest.mark.asyncio
async def test_owner_chat_send_still_requires_csrf_before_runtime():
    ns = chat_namespace()
    ns["ensure_csrf"] = lambda *a: False
    response = await ns["chat_send"](
        NS(state=NS(web_perms=NS(can_open_chat=True)), headers={"accept": "application/json"}), "text", "invalid"
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_owner_history_uses_own_user_session_without_starting_turn(monkeypatch):
    session = AsyncMock(return_value=22)
    messages = AsyncMock(return_value=[])
    module(monkeypatch, "app.agent.runtime", web_session_id=session)
    module(monkeypatch, "app.models.managers.agent_message_manager", agent_messages=NS(recent=messages))
    ns = chat_namespace()
    ns.update(
        TRANSCRIPT_LIMIT=40,
        _display_transcript=lambda items: items,
        _is_owner=lambda req: True,
        _confirmation_pending=AsyncMock(return_value=False),
    )
    response = await ns["chat_page"](
        NS(
            state=NS(web_perms=NS(can_open_chat=True), web_user=user(), tenant_id=7),
            headers={"accept": "application/json"},
        )
    )
    assert response.status_code == 200
    session.assert_awaited_once_with(101)
    messages.assert_awaited_once_with(22, limit=40)
