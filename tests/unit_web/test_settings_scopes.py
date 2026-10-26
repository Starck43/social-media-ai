"""AST-extracted handlers and fake ORM boundaries only; never import app or use DB/ASGI."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace as NS

import pytest
from starlette.responses import HTMLResponse, RedirectResponse

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "app/web/settings.py"


def extracted():
    names = {
        "_personal_access",
        "_platform_access",
        "_workspace_access",
        "_visible_tabs",
        "_visible_tab",
        "_tab_of",
        "settings_page",
        "_plan_usage",
        "_guard_workspace_settings",
        "workspace_update",
        "channel_update",
        "credential_disable",
        "credential_store",
    }
    nodes = [
        n
        for n in ast.parse(SOURCE.read_text()).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names
    ]
    for node in nodes:
        node.decorator_list = []
        node.args.defaults = [ast.Constant(None) for _ in node.args.defaults]
        node.args.kw_defaults = [ast.Constant(None) if n is not None else None for n in node.args.kw_defaults]
    ns = {
        "TABS": ("account", "connections", "workspace", "team", "agent", "channels", "platforms"),
        "MANAGER_TABS": frozenset({"agent", "channels"}),
        "BACK": "/app/settings",
        "HTMLResponse": HTMLResponse,
        "RedirectResponse": RedirectResponse,
        "ROLE_LABELS": {"viewer": "Viewer"},
        "SELF_SERVICE_KINDS": {"vk": (("user_token", "Token"),)},
        "ActionType": NS(choices=lambda: []),
        "member_roles": NS(key_of=lambda m: "viewer"),
        "Tenant": NS(PLANS=["free"]),
        "_plan_labels": lambda: {"free": "Free"},
        "_plan_table": lambda: [],
        "add_flash": lambda *args: None,
        "ensure_csrf": lambda request, token: token == "mock-csrf",
        "action_tenant_id": lambda request, posted: request.state.tenant_id,
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
        ns,
    )
    return ns


class Perms:
    def __init__(self, manage=False, rights=()):
        self.manage, self.rights = manage, rights

    def can_manage_workspace(self, target):
        return self.manage and target == 7

    def can(self, model, verb):
        return (model, verb) in self.rights


def request(
    tab="account",
    *,
    manage=False,
    active=True,
    membership=True,
    user_id=101,
    tenant_id=7,
    tenant_active=True,
    operator=False,
    rights=(),
):
    tenant = NS(
        id=7,
        is_active=tenant_active,
        name="Workspace",
        timezone="Europe/Kirov",
        effective_limits=lambda: {"max_sources": 3, "max_channels": 5, "max_team_members": 5},
    )
    user = NS(
        id=user_id,
        is_active=active,
        is_platform_admin=operator,
        has_admin_access=lambda: bool(active and operator),
    )
    member = NS(id=3, user_id=101, tenant_id=7, channel="web", is_active=True)
    return NS(
        state=NS(
            tenant_id=tenant_id,
            tenant=tenant,
            web_user=user,
            memberships=[member] if membership else [],
            web_perms=Perms(manage, rights),
        ),
        query_params={"tab": tab},
        session={},
    )


@pytest.fixture
def gateway(monkeypatch):
    calls = []

    class Query:
        def __init__(self, name, rows=None):
            self.name, self.items = name, rows or []

        def __await__(self):
            async def rows():
                return self.items

            return rows().__await__()

        def filter(self, **kwargs):
            calls.append((self.name, "filter", kwargs))
            return self

        def select_related(self, *args):
            calls.append((self.name, "related", args))
            return self

        def order_by(self, *args):
            return self

        async def count(self):
            calls.append((self.name, "count"))
            return 1

    class Members:
        async def web_memberships_for_tenant(self, tid):
            calls.append(("members", tid))
            return [NS(id=3, user_id=101)]

    class Vault:
        async def web_metadata_for_user(self, **kwargs):
            calls.append(("vault", kwargs))
            return [NS(platform="vk", kind="user_token", id=4, label="Mine", is_active=True)]

        async def get(self, **kwargs):
            calls.append(("vault-get", kwargs))
            return None

        async def store(self, **kwargs):
            calls.append(("vault-store", kwargs))

    async def statuses(uid):
        calls.append(("statuses", uid))
        return [NS(platform="vk", state="reauth")]

    fake_models = {
        key: NS(objects=Query(key), id="id") for key in ["source", "agenttask", "agentscenario", "models", "users"]
    }
    modules = {
        "app.models.managers.tenant_manager": dict(
            TenantUserManager=Members, TenantChannelManager=lambda: Query("channels")
        ),
        "app.models.identity.user": dict(User=fake_models["users"]),
        "app.models.llm.llm_model": dict(LLMModel=fake_models["models"]),
        "app.models.scheduling.agent_task": dict(AgentTask=fake_models["agenttask"]),
        "app.models.analysis.agent_scenario": dict(AgentScenario=fake_models["agentscenario"]),
        "app.models.collection.source": dict(Source=fake_models["source"]),
        "app.models.managers.user_credential_manager": dict(user_credentials=Vault()),
        "app.services.social.connections": dict(
            CONNECTIONS=[NS(platform="vk", title="VK", purpose="Own", is_oauth=True, manual_hint="")],
            connection_statuses=statuses,
        ),
        "app.services.ai.llm_client": dict(default_model_sort_key=lambda m: m.name),
    }
    for name, values in modules.items():
        module = ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    ns = extracted()

    def render(request, template, **context):
        return NS(headers={}, context=context)

    ns["render"] = render
    return ns, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("tab", ["account", "workspace", "team", "agent", "channels"])
async def test_member_does_not_query_other_people_or_hidden_configuration(gateway, tab):
    ns, calls = gateway
    response = await ns["settings_page"](request(tab))
    assert calls == []
    assert response.headers["Cache-Control"] == "no-store"
    assert response.context["tab"] == ("account" if tab in ("agent", "channels") else tab)
    assert response.context["settings_team_visible"] is False
    if tab == "workspace":
        assert response.context["workspace_summary"] == {"name": "Workspace", "timezone": "Europe/Kirov"}
        assert not set(response.context) & {"plan_usage", "agent_model_options", "channels", "memberships"}


@pytest.mark.asyncio
@pytest.mark.parametrize("manage", [False, True])
@pytest.mark.parametrize("tab", ["connections", "credentials"])
async def test_personal_connections_always_use_caller_id_metadata_only(gateway, manage, tab):
    ns, calls = gateway
    response = await ns["settings_page"](request(tab, manage=manage))
    assert calls == [("vault", {"user_id": 101}), ("statuses", 101)]
    assert response.context["tab"] == "connections"
    assert response.context["connections"][0]["rows"][0].label == "Mine"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tab,allowed",
    [
        ("account", set()),
        ("team", {"members", "users"}),
        ("agent", {"models", "channels", "members"}),
        ("channels", {"channels"}),
    ],
)
async def test_manager_selected_tab_only(gateway, tab, allowed):
    ns, calls = gateway
    response = await ns["settings_page"](request(tab, manage=True))
    assert {c[0] for c in calls} <= allowed
    assert response.context["tab"] == tab
    if tab == "team":
        assert calls[0] == ("members", 7)
    if tab == "channels":
        assert calls == [("channels", "filter", {"tenant_id": 7})]


@pytest.mark.asyncio
async def test_manager_workspace_quota_queries_require_resource_view(gateway):
    ns, calls = gateway
    response = await ns["settings_page"](request("workspace", manage=True, rights={("source", "view")}))
    assert "max_sources" in response.context["plan_usage"]
    assert {c[0] for c in calls} == {"source", "members", "channels"}
    assert ("source", "filter", {"tenant_id": 7}) in calls
    assert "max_tasks" not in response.context["plan_usage"]


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [{"membership": False}, {"tenant_id": 8}, {"tenant_active": False}])
async def test_stale_or_foreign_workspace_falls_back_to_own_account_without_queries(gateway, options):
    ns, calls = gateway
    response = await ns["settings_page"](request("agent", manage=True, **options))
    assert response.context["visible_tabs"] == ("account",)
    assert response.context["settings_manage"] is False
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [{"active": False}, {"user_id": None}, {"user_id": True}, {"user_id": 0}])
async def test_unknown_or_inactive_user_fails_closed(gateway, options):
    ns, calls = gateway
    response = await ns["settings_page"](request(**options))
    assert response.status_code == 403 and calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "function,kwargs",
    [
        ("workspace_update", dict(name="New", timezone="Europe/Kirov", daily_cost_limit="1", tenant_id=7)),
        ("channel_update", dict(channel_id=9, is_digest_target="on")),
    ],
)
@pytest.mark.parametrize("manage,token", [(False, "mock-csrf"), (True, "invalid")])
async def test_existing_workspace_mutations_require_fresh_capability_and_csrf(gateway, function, kwargs, manage, token):
    ns, calls = gateway
    response = await ns[function](request(manage=manage), token=token, **kwargs)
    assert response.status_code == 302 and calls == []


@pytest.mark.asyncio
async def test_personal_credential_id_never_changes_user_scope(gateway):
    ns, calls = gateway
    response = await ns["credential_disable"](request(manage=True), credential_id=999, token="mock-csrf")
    assert calls == [("vault-get", {"id": 999, "user_id": 101})]
    assert response.status_code == 302


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "function,kwargs",
    [
        ("credential_disable", {"credential_id": 1}),
        ("credential_store", {"kind": "vk::user_token", "secret": "fictitious", "label": "mock"}),
    ],
)
async def test_inactive_personal_mutations_never_reach_vault(gateway, function, kwargs):
    ns, calls = gateway
    await ns[function](request(active=False), token="mock-csrf", **kwargs)
    assert calls == []


def test_old_bookmarks_keep_semantics():
    ns = extracted()
    assert ns["_tab_of"](request("../../admin")) == "workspace"
    assert ns["_tab_of"](request("credentials")) == "connections"
    assert ns["_tab_of"](request("account")) == "account"


def test_no_workspace_exception_is_limited_to_personal_account_and_settings_read():
    tree = ast.parse((ROOT / "app/web/middleware.py").read_text())
    condition = next(
        n.test
        for n in ast.walk(tree)
        if isinstance(n, ast.If)
        and "NO_WORKSPACE_PATHS" in ast.unparse(n.test)
        and "memberships" in ast.unparse(n.test)
    )
    code = compile(ast.Expression(condition), "middleware no-workspace guard", "eval")
    for method, path, redirect in [
        ("GET", "/app/settings", False),
        ("HEAD", "/app/settings/", False),
        ("GET", "/app/billing/account", False),
        ("POST", "/app/billing/account", False),
        ("POST", "/app/billing/account/daily-limit", False),
        ("POST", "/app/billing/tasks/7/payer", True),
        ("POST", "/app/settings/workspace", True),
        ("GET", "/app/settings/invites", True),
        ("GET", "/app/tasks", True),
    ]:
        assert (
            bool(
                eval(
                    code,
                    {
                        "user": object(),
                        "memberships": [],
                        "path": path,
                        "request": NS(method=method),
                        "NO_WORKSPACE_PATHS": {"/app/invite", "/app/workspaces/new", "/app/logout"},
                        "PERSONAL_ACCOUNT_PATHS": {
                            "/app/notifications/preferences",
                            "/app/notifications/preferences/reset",
                            "/app/billing/account",
                            "/app/billing/account/daily-limit",
                        },
                    },
                )
            )
            is redirect
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id,queries", [(101, True), (None, False), (True, False), (0, False)])
async def test_vault_ui_gateway_projects_only_bounded_caller_metadata(user_id, queries):
    source = ast.parse((ROOT / "app/models/managers/user_credential_manager.py").read_text())
    cls = next(n for n in source.body if isinstance(n, ast.ClassDef))
    node = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "web_metadata_for_user")
    # Replace only the ORM import with explicit fake columns, not runtime model registration.
    node.body = [n for n in node.body if not isinstance(n, ast.ImportFrom)]
    calls = []

    class Column(str):
        def desc(self):
            return self + ":desc"

    class Fake:
        def filter(self, **kwargs):
            calls.append(("filter", kwargs))
            return self

        def order_by(self, *args):
            calls.append(("order", args))
            return self

        def limit(self, size):
            calls.append(("limit", size))
            return self

        def values(self, *args):
            calls.append(("values", args))
            return self

        async def rows(self):
            return []

    ns = {"UserCredential": NS(**{key: Column(key) for key in ["id", "platform", "kind", "label", "is_active"]})}
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(
                    body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node],
                    type_ignores=[],
                )
            ),
            "fake vault projection",
            "exec",
        ),
        ns,
    )
    await ns["web_metadata_for_user"](Fake(), user_id=user_id)
    if not queries:
        assert calls == []
    else:
        assert calls[0] == ("filter", {"user_id": 101})
        assert ("limit", 100) in calls
        assert next(c[1] for c in calls if c[0] == "values") == ("id", "platform", "kind", "label", "is_active")
        assert "secret_encrypted" not in repr(calls)


@pytest.mark.asyncio
async def test_platform_admin_does_not_gain_web_bypass_or_settings_mutation(gateway):
    ns, calls = gateway
    owner = await ns["settings_page"](request("account", manage=True))
    operator = await ns["settings_page"](request("account", manage=False, operator=True))
    assert owner.context["is_superuser"] is False
    assert operator.context["is_superuser"] is False
    assert operator.context["settings_manage"] is False
    assert "platforms" in operator.context["visible_tabs"]
    assert "platforms" not in owner.context["visible_tabs"]
    assert calls == []


def test_member_templates_have_no_hidden_budget_team_or_agent_fields():
    import runpy

    helpers = runpy.run_path(str(ROOT / "tests/unit_web/test_assistant_shell.py"))
    values = helpers["context"](manager=False)
    values["user"].has_admin_access = lambda: False
    values["user"].role = None
    values.update(
        is_superuser=False,
        settings_manage=False,
        settings_team_visible=False,
        visible_tabs=["account", "connections", "workspace", "team"],
    )
    values.update(tab="workspace", workspace_summary={"name": "<img src=x onerror=bad()>", "timezone": "Europe/Kirov"})
    markup = helpers["environment"]().get_template("web/settings.html").render(**values)
    assert "data-workspace-readonly" in markup
    assert "&lt;img src=x" in markup and "<img src=x" not in markup
    for forbidden in ['name="daily_cost_limit"', 'name="agent_system_prompt"', 'action="/app/settings/workspace"']:
        assert forbidden not in markup
    values.update(tab="team", member_names={999: "Forbidden member"}, memberships=[NS(user_id=999)])
    markup = helpers["environment"]().get_template("web/settings.html").render(**values)
    assert "Forbidden member" not in markup and "<table" not in markup
    assert 'href="/app/invite"' in markup


def test_no_workspace_account_menu_keeps_personal_settings_reachable():
    import runpy

    helpers = runpy.run_path(str(ROOT / "tests/unit_web/test_assistant_shell.py"))
    values = helpers["context"](tenant=False)
    values["user"].has_admin_access = lambda: False
    values["user"].role = None
    markup = helpers["environment"]().get_template("shell-fixture.html").render(**values)
    assert 'href="/app/settings?tab=account"' in markup
    assert "data-invite-open" not in markup
