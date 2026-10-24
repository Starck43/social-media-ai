"""Role-based access control of the operator console (`/admin`).

The console is gated by the platform role of the signed-in operator, Django
style, and the rules live in four places: `User.model_permissions` (what the
role grants), the `AdminAuthorizationBackend` (that turns those rights into the
`(identity, action)` pairs sqladmin asks about), the `can_*` flags and
`action_permissions` of each view (what the view offers at all), and the menu,
which follows from the same grants.

The first half pins the grant derivation against a stub user — none of those
rules need a database. The second half signs in over ASGI for real and checks
that the menu, the buttons and the routes behind them give the same answer,
because a gate that only hides a button is not a gate.
"""

import re
import secrets
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqladmin.authorization import Action, custom_action
from sqladmin.models import ModelView

from app.admin.authorization import (
    BUILTIN_ACTION_TYPES,
    AdminAuthorizationBackend,
    _custom_action_types,
)
from app.admin.views import (
    AgentScenarioAdmin,
    AgentTaskAdmin,
    AIAnalyticsAdmin,
    LLMProviderAdmin,
    NotificationAdmin,
    SourceAdmin,
    UserAdmin,
)
from app.core.tenant_context import tenant_scope
from app.models import Permission, Role, User
from app.types import ActionType, PlatformRoleType

# The views the derivation is exercised against: the ones with a custom action,
# plus a plain CRUD one and a read-only one.
VIEW_CLASSES = (
    UserAdmin,
    SourceAdmin,
    AIAnalyticsAdmin,
    AgentScenarioAdmin,
    AgentTaskAdmin,
    NotificationAdmin,
    LLMProviderAdmin,
)


class _StubUser:
    """A user with a fixed set of rights, without a database behind it."""

    def __init__(self, perms: dict[str, set[ActionType]] | None = None, *, superuser: bool = False):
        self._perms = {name.lower(): set(actions) for name, actions in (perms or {}).items()}
        self._superuser = superuser

    def _is_superuser_role(self) -> bool:
        return self._superuser

    def has_admin_access(self) -> bool:
        """Mirror the real `User.has_admin_access`: an active platform operator.

        Every stub stands for a signed-in operator; the *rights* differ, not
        the access to the console. The admin backend asks this before it
        computes any grant, so a stub without it fails with an
        AttributeError instead of answering the question.
        """
        return True

    @property
    def is_platform_admin(self) -> bool:
        """Mirror the real `User.is_platform_admin`: the platform admin role.

        A platform admin passes every gate (the wildcard grant); an operator
        with narrower rights does not. It is a property on the model, so the
        stub must be one too — a bound method would always be truthy.
        """
        return self._superuser

    def model_permissions(self, model_name: str) -> set[ActionType]:
        if self._superuser:
            return set(ActionType)
        return set(self._perms.get(model_name.lower(), set()))

    def has_perm_for(self, model_name: str, action: ActionType) -> bool:
        return action in self.model_permissions(model_name)


def _console(user: _StubUser | None = None) -> tuple[AdminAuthorizationBackend, SimpleNamespace]:
    """A backend with the views registered, plus a request to ask it with."""
    views = [view_class() for view_class in VIEW_CLASSES]
    backend = AdminAuthorizationBackend()
    backend.setup(SimpleNamespace(_views=views))
    request = SimpleNamespace(
        state=SimpleNamespace(),
        session={},
        app=SimpleNamespace(state=SimpleNamespace(admin=SimpleNamespace(_views=views))),
    )
    if user is not None:
        # `resolve_user` caches the operator on `request.state`, which is where
        # the console reads it from; this is what `load()` would have put there.
        request.state.admin_user = user
    return backend, request


async def _loaded(user: _StubUser | None = None) -> tuple[AdminAuthorizationBackend, SimpleNamespace]:
    backend, request = _console(user)
    await backend.load(request)
    return backend, request


# --- grant derivation ------------------------------------------------------


async def test_an_anonymous_request_is_granted_nothing():
    backend, request = await _loaded(None)
    for action in Action:
        assert not backend.has_permission(request, "source", action)


async def test_a_superuser_passes_everything():
    backend, request = await _loaded(_StubUser(superuser=True))
    for action in Action:
        assert backend.has_permission(request, "user", action)
    assert backend.has_permission(request, "not-a-registered-view", "delete")


async def test_view_grants_the_list_and_the_details_only():
    backend, request = await _loaded(_StubUser({"source": {ActionType.VIEW}}))
    assert backend.has_permission(request, "source", Action.LIST)
    assert backend.has_permission(request, "source", Action.DETAILS)


async def test_each_action_needs_its_own_right():
    for action_name, action_type in BUILTIN_ACTION_TYPES.items():
        backend, request = await _loaded(_StubUser({"source": {action_type}}))
        assert backend.has_permission(request, "source", action_name), action_name
        for other_name, other_type in BUILTIN_ACTION_TYPES.items():
            if other_name == action_name or other_type == action_type:
                continue
            if other_name in (Action.LIST, Action.DETAILS):
                continue  # implied by any right — pinned by the test above
            assert not backend.has_permission(request, "source", other_name), (action_name, other_name)


async def test_an_import_is_a_create():
    """The import form writes rows exactly like the create form does."""
    backend, request = await _loaded(_StubUser({"source": {ActionType.UPDATE}}))
    assert not backend.has_permission(request, "source", Action.IMPORT)
    backend, request = await _loaded(_StubUser({"source": {ActionType.CREATE}}))
    assert backend.has_permission(request, "source", Action.IMPORT)


async def test_a_custom_action_needs_the_right_its_view_declares():
    """Running a task is a mutation and probing a provider is configuration —
    neither may ride in on a plain `view`."""
    viewer, request = await _loaded(_StubUser({"agenttask": {ActionType.VIEW}, "llmprovider": {ActionType.VIEW}}))
    assert not viewer.has_permission(request, "agent-task", custom_action("run-now"))
    assert not viewer.has_permission(request, "llm-provider", custom_action("test-connection"))

    editor, request = await _loaded(_StubUser({"agenttask": {ActionType.UPDATE}}))
    assert editor.has_permission(request, "agent-task", custom_action("run-now"))
    assert not editor.has_permission(request, "llm-provider", custom_action("test-connection"))

    configurer, request = await _loaded(_StubUser({"llmprovider": {ActionType.CONFIGURE}}))
    assert configurer.has_permission(request, "llm-provider", custom_action("test-connection"))
    assert not configurer.has_permission(request, "llm-provider", Action.EDIT)


async def test_an_unknown_view_is_granted_nothing():
    backend, request = await _loaded(_StubUser({"source": {ActionType.VIEW}}))
    assert not backend.has_permission(request, "not-a-registered-view", Action.LIST)


def test_a_view_declares_every_custom_action_it_has():
    """A key spelled like the function instead of the slug silently falls back to
    the default right, so the declarations are checked against the views."""
    for view_class in VIEW_CLASSES:
        view = view_class()
        assert isinstance(view, ModelView)
        declared = getattr(view, "action_permissions", None) or {}
        actions = _custom_action_types(view)
        assert actions, f"{view_class.__name__} declares no custom action"
        assert not set(declared) - set(actions), f"{view_class.__name__}: {set(declared) - set(actions)}"


def test_the_catalogue_reads_the_model_name_off_the_views():
    """`AgentTask` is the identity `agent-task` and the `model_types` row
    `agenttask`; the mapping is derived, so a new view needs no entry."""
    backend, request = _console()
    model_by_identity, custom = backend._catalog(request)

    assert model_by_identity["agent-task"] == "agenttask"
    assert model_by_identity["ai-analytics"] == "aianalytics"
    assert model_by_identity["llm-provider"] == "llmprovider"
    assert custom["notification"]["mark-read"] is ActionType.UPDATE
    assert custom["llm-provider"]["test-connection"] is ActionType.CONFIGURE
    assert custom["agent-scenario"]["view-prompts"] is ActionType.VIEW

    for action in (Action.CREATE, Action.EDIT, Action.DELETE, Action.EXPORT, Action.IMPORT):
        assert not backend.has_permission(request, "source", action), action


async def test_a_model_the_role_does_not_cover_is_not_granted_at_all():
    """No right on the model means no menu entry and no reachable route."""
    backend, request = await _loaded(_StubUser({"source": {ActionType.VIEW}}))
    for action in Action:
        assert not backend.has_permission(request, "user", action), action


async def test_any_other_right_implies_the_change_list():
    """Django's `has_view_permission` is true for add/change/delete as well."""
    backend, request = await _loaded(_StubUser({"source": {ActionType.CREATE}}))
    assert backend.has_permission(request, "source", Action.CREATE)
    assert backend.has_permission(request, "source", Action.LIST)
    assert backend.has_permission(request, "source", Action.DETAILS)
    assert not backend.has_permission(request, "source", Action.EDIT)


# --- the console as a signed-in operator sees it --------------------------

PASSWORD = "secret-password-1"


def _uniq(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4)}"


async def _operator(role: Role) -> str:
    """A user holding the given platform role; returns the username."""
    username = _uniq("op")
    await User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=PASSWORD,
        role_id=role.id,
    )
    return username


async def _operator_with_workspace(role: Role) -> str:
    """The same operator, plus a web membership of its own workspace.

    A workspace page lists rows of a workspace, so the actor needs one: the
    console scope is the membership, exactly as in `/app` and `/api`
    (`docs/ADMIN.md` — "everyone else their own").
    """
    from app.models import Tenant
    from app.models.managers.tenant_manager import TenantUserManager

    username = await _operator(role)
    user = await User.objects.get(username=username)
    with tenant_scope(bypass=True):
        slug = _uniq("ws")
        workspace = await Tenant.objects.create(name=slug, slug=slug)
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=user.id, role="owner")
    return username


async def _role(codename: str) -> Role:
    return await Role.objects.get(codename=PlatformRoleType[codename].value)


@asynccontextmanager
async def _signed_in(username: str) -> AsyncIterator[AsyncClient]:
    """An ASGI client whose session cookie belongs to `username`."""
    from app.main import create_application

    async with AsyncClient(transport=ASGITransport(app=create_application()), base_url="http://testserver") as client:
        page = await client.get("/admin/login")
        token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text).group(1)
        await client.post(
            "/admin/login",
            data={"username": username, "password": PASSWORD, "csrf_token": token},
            follow_redirects=True,
        )
        yield client


@pytest.mark.tenancy
async def test_a_viewer_opens_the_models_its_role_covers():
    role = await _role("OPERATOR")
    async with _restricted(
        role,
        lambda permission: permission.action_type is ActionType.VIEW
        and permission.model_type.resource_key in {"collection.source", "collection.platform"},
    ):
        async with _signed_in(await _operator_with_workspace(role)) as client:
            assert (await client.get("/admin/source/list")).status_code == 200
            assert (await client.get("/admin/platform/list")).status_code == 200


async def test_a_viewer_is_refused_the_models_its_role_does_not_cover():
    """The role grants no `user` right at all: no menu entry, no route.

    A "viewer" in the console is a platform operator whose catalog rights are
    restricted to the models it may read — the platform roles themselves are
    user/operator/admin, and the workspace roles (viewer, analyst, …) are a
    different axis.
    """
    role = await _role("OPERATOR")
    async with _restricted(
        role,
        lambda permission: permission.action_type is ActionType.VIEW
        and permission.model_type.resource_key in {"collection.source", "collection.platform"},
    ):
        async with _signed_in(await _operator(role)) as client:
            for url in ("/admin/user/list", "/admin/platform-role/list", "/admin/agent-task/list"):
                assert (await client.get(url)).status_code == 403, url


async def test_the_menu_follows_the_same_grants_as_the_routes():
    role = await _role("OPERATOR")
    async with _restricted(
        role,
        lambda permission: permission.action_type is ActionType.VIEW
        and permission.model_type.resource_key in {"collection.source", "collection.platform"},
    ):
        async with _signed_in(await _operator(role)) as client:
            index = await client.get("/admin/", follow_redirects=True)
    assert index.status_code == 200
    assert "/admin/source/list" in index.text
    assert "/admin/user/list" not in index.text


@pytest.mark.tenancy
async def test_a_viewer_cannot_write_and_cannot_run_a_task():
    role = await _role("OPERATOR")
    async with _restricted(
        role,
        lambda permission: permission.action_type is ActionType.VIEW
        and permission.model_type.resource_key == "collection.source",
    ):
        async with _signed_in(await _operator(role)) as client:
            assert (await client.get("/admin/source/create")).status_code == 403
            assert (await client.get("/admin/agent-task/action/run-now?pks=1")).status_code == 403


@pytest.mark.tenancy
async def test_an_admin_opens_the_platform_catalogs():
    """A platform admin reaches the account/workspace catalogs it administers."""
    async with _signed_in(await _operator(await _role("ADMIN"))) as client:
        for url in (
            "/admin/user/list",
            "/admin/platform-role/list",
            "/admin/tenant/list",
        ):
            assert (await client.get(url)).status_code == 200, url


@pytest.mark.tenancy
async def test_an_admin_without_a_workspace_is_refused_workspace_data_not_500():
    """`admin` is staff, not the owner: a workspace page is a refusal, not a crash.

    The owner is the only actor that sees every workspace (`AdminScopeMiddleware`
    keeps the bypass for `superuser` only). An admin who belongs to no workspace
    has no workspace rows, so the page must say so rather than run the manager
    against an empty tenant context.
    """
    async with _signed_in(await _operator(await _role("ADMIN"))) as client:
        assert (await client.get("/admin/agent-task/list")).status_code == 403


@pytest.mark.tenancy
async def test_the_owner_sees_every_model():
    """Only the `superuser` account keeps the console-wide bypass."""
    username = await _operator(await _role("ADMIN"))
    # Promote through the reviewed migration, not a console/API grant: no runtime
    # path may hand out `superuser`, so the fixture writes the role row directly.
    from app.models import PlatformRole

    superuser_role = await PlatformRole.objects.get(codename=PlatformRoleType.SUPERUSER.value)
    user = await User.objects.get(username=username)
    await User.objects.update_by_id(user.id, role_id=superuser_role.id)

    async with _signed_in(username) as client:
        for url in (
            "/admin/user/list",
            "/admin/platform-role/list",
            "/admin/tenant/list",
            "/admin/agent-task/list",
        ):
            assert (await client.get(url)).status_code == 200, url


@pytest.mark.tenancy
async def test_a_regular_user_cannot_enter_the_console():
    """An ordinary platform user cannot authenticate to the operator console."""
    async with _signed_in(await _operator(await _role("USER"))) as client:
        response = await client.get("/admin/source/list", follow_redirects=False)
    assert response.status_code in (302, 303)
    assert "/admin/login" in response.headers.get("location", "")


@asynccontextmanager
async def _restricted(role: Role, keep) -> AsyncIterator[Role]:
    """Give `role` selected catalog permissions and restore its prior grants."""
    loaded = await Role.objects.get_with_permissions(role.id)
    original = [permission.id for permission in loaded.permissions]
    catalog = await Permission.objects.prefetch_related("model_type").all()
    selected = [permission.id for permission in catalog if keep(permission)]
    with tenant_scope(bypass=True):
        await Role.objects.set_permissions(role.id, selected)
    try:
        yield role
    finally:
        with tenant_scope(bypass=True):
            await Role.objects.set_permissions(role.id, original)


async def test_the_dashboard_outside_the_console_asks_the_same_question():
    """`/dashboard` is a plain route in `app.admin.endpoints`, so nothing would
    have stopped it from showing analytics to a role without the right."""
    role = await _role("OPERATOR")

    def sources_only(permission) -> bool:
        return permission.model_type is not None and permission.model_type.resource_key == "collection.source"

    async with _restricted(role, sources_only):
        async with _signed_in(await _operator(role)) as client:
            analytics = await client.get("/admin/ai-analytics/list", follow_redirects=False)
            dashboard = await client.get("/dashboard", follow_redirects=False)

    assert analytics.status_code == 403
    assert dashboard.status_code == 403
