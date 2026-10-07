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

from httpx import ASGITransport, AsyncClient
from sqladmin.authorization import Action, custom_action
from sqladmin.models import ModelView

from app.admin.authorization import (
    BUILTIN_ACTION_TYPES,
    AdminAuthorizationBackend,
    _custom_action_types,
)
from app.admin.views import (
    AIAnalyticsAdmin,
    AgentScenarioAdmin,
    AgentTaskAdmin,
    LLMProviderAdmin,
    NotificationAdmin,
    SourceAdmin,
    UserAdmin,
)
from app.models import Role, User
from app.types import ActionType, UserRoleType

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
    viewer, request = await _loaded(
        _StubUser({"agenttask": {ActionType.VIEW}, "llmprovider": {ActionType.VIEW}})
    )
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


async def _role(codename: str) -> Role:
    return await Role.objects.get(codename=UserRoleType[codename].name)


@asynccontextmanager
async def _signed_in(username: str) -> AsyncIterator[AsyncClient]:
    """An ASGI client whose session cookie belongs to `username`."""
    from app.main import create_application

    async with AsyncClient(
        transport=ASGITransport(app=create_application()), base_url="http://testserver"
    ) as client:
        page = await client.get("/admin/login")
        token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text).group(1)
        await client.post(
            "/admin/login",
            data={"username": username, "password": PASSWORD, "csrf_token": token},
            follow_redirects=True,
        )
        yield client


async def test_a_viewer_opens_the_models_its_role_covers():
    async with _signed_in(await _operator(await _role("VIEWER"))) as client:
        assert (await client.get("/admin/source/list")).status_code == 200
        assert (await client.get("/admin/platform/list")).status_code == 200


async def test_a_viewer_is_refused_the_models_its_role_does_not_cover():
    """The role grants no `user` right at all: no menu entry, no route."""
    async with _signed_in(await _operator(await _role("VIEWER"))) as client:
        for url in ("/admin/user/list", "/admin/role/list", "/admin/agent-task/list"):
            assert (await client.get(url)).status_code == 403, url


async def test_the_menu_follows_the_same_grants_as_the_routes():
    async with _signed_in(await _operator(await _role("VIEWER"))) as client:
        index = await client.get("/admin/", follow_redirects=True)
    assert index.status_code == 200
    assert "/admin/source/list" in index.text
    assert "/admin/user/list" not in index.text


async def test_a_viewer_cannot_write_and_cannot_run_a_task():
    async with _signed_in(await _operator(await _role("VIEWER"))) as client:
        assert (await client.get("/admin/source/create")).status_code == 403
        assert (await client.get("/admin/agent-task/action/run-now?pks=1")).status_code == 403


async def test_a_superuser_sees_every_model():
    async with _signed_in(await _operator(await _role("SUPERUSER"))) as client:
        for url in ("/admin/user/list", "/admin/role/list", "/admin/tenant/list", "/admin/agent-task/list"):
            assert (await client.get(url)).status_code == 200, url


async def test_a_role_with_no_rights_at_all_cannot_enter_the_console():
    """The matrix in `scripts/setup/assign_roles_permissions.py` gives every role
    at least one `view`, so the empty case has to be built for the test."""
    role = await _role("VIEWER")
    original = [p.id for p in (await Role.objects.get_with_permissions(role.id)).permissions]
    await Role.objects.set_permissions(role.id, [])
    # The check the whole test rests on: a no-op strip would turn it into a
    # duplicate of the viewer tests above, and they would still pass.
    assert not (await Role.objects.get_with_permissions(role.id)).permissions

    try:
        async with _signed_in(await _operator(role)) as client:
            response = await client.get("/admin/source/list", follow_redirects=False)
    finally:
        await Role.objects.set_permissions(role.id, original)

    assert response.status_code in (302, 303)
    assert "/admin/login" in response.headers.get("location", "")


@asynccontextmanager
async def _restricted(role: Role, keep) -> AsyncIterator[Role]:
    """Give `role` exactly the permissions `keep(permission)` selects.

    The matrix in `scripts/setup/assign_roles_permissions.py` hands every
    platform role a bit of everything, so a role that may read the sources but
    not the analytics — the case the gates actually have to tell apart — has to
    be built for the test.
    """
    loaded = await Role.objects.get_with_permissions(role.id)
    original = [permission.id for permission in loaded.permissions]
    await Role.objects.set_permissions(role.id, [p.id for p in loaded.permissions if keep(p)])
    try:
        yield role
    finally:
        await Role.objects.set_permissions(role.id, original)


async def test_the_dashboard_outside_the_console_asks_the_same_question():
    """`/dashboard` is a plain route in `app.admin.endpoints`, so nothing would
    have stopped it from showing analytics to a role without the right."""
    role = await _role("VIEWER")

    def sources_only(permission) -> bool:
        return permission.model_type is not None and permission.model_type.model_name == "source"

    async with _restricted(role, sources_only):
        async with _signed_in(await _operator(role)) as client:
            analytics = await client.get("/admin/ai-analytics/list", follow_redirects=False)
            dashboard = await client.get("/dashboard", follow_redirects=False)

    assert analytics.status_code == 403
    assert dashboard.status_code == 403

