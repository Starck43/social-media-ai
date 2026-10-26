"""Admin console scope: one request, one selected workspace, one personal catalogue.

`AdminScopeMiddleware` replaced the console-wide platform bypass. These tests pin
the decision it makes per request — which workspace the request runs in, which
membership role caps it, and whose personal catalogue is bound — without a
database: the identity, the membership list and the contexts are all explicit
doubles.

The owner keeps platform-level workspace inspection (every workspace row), but
that bypass never widens a personal catalogue: providers and models stay
`owner_user_id`-scoped for every platform role, the owner included.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.admin.scope import AdminScopeMiddleware
from app.core.permissions import get_current_user, has_permission
from app.core.personal_catalog_context import current_catalog_owner_id
from app.core.tenant_context import (
    PlatformScopeMiddleware,
    current_tenant_id,
    current_tenant_ids,
    is_bypass,
    tenant_scope,
)

ROOT = Path(__file__).resolve().parents[2]

# The questions the probe answers about the live scope, by name. `has_permission`
# needs the actor *and* the membership grants the middleware entered, so they are
# evaluated inside the app call and kept under the same keys as the scope facts.
CAPABILITIES = {
    "can_view_source": ("source", "view"),
    "can_update_source": ("source", "update"),
}


class _Probe:
    """Records the ambient scope an admin request actually runs in."""

    def __init__(self):
        self.seen: dict = {}
        self.capabilities: dict = {}

    async def __call__(self, scope, receive, send):
        actor = get_current_user()
        self.seen = {
            "bypass": is_bypass(),
            "tenant": current_tenant_id(),
            "tenants": current_tenant_ids(),
            "actor": getattr(actor, "id", None),
            "catalog_owner": current_catalog_owner_id() if actor is not None else None,
        }
        # Asked here, inside the scope the middleware entered: `has_permission`
        # reads the actor *and* the selected membership from these same context
        # vars, so this is the role the request really runs under.
        self.capabilities = {
            key: bool(actor is not None and has_permission(actor, model_name, action))
            for key, (model_name, action) in CAPABILITIES.items()
        }


def _user(user_id=7, *, superuser=False):
    return SimpleNamespace(id=user_id, is_active=True, is_superuser=superuser)


def _request(path="/admin/source/list", tenant_id=None):
    session = {}
    if tenant_id is not None:
        session["app_tenant_id"] = tenant_id
    return {"type": "http", "path": path, "method": "GET", "headers": [], "session": session}


def _memberships(monkeypatch, rows):
    """`rows`: (tenant_id, grants) the actor holds; a single membership is enough."""

    async def web_memberships(self, user_id):
        return [SimpleNamespace(tenant_id=tenant_id) for tenant_id, _ in rows]

    async def member_grants(self, membership):
        return next(grants for tenant_id, grants in rows if tenant_id == membership.tenant_id)

    monkeypatch.setattr("app.models.managers.tenant_manager.TenantUserManager.web_memberships", web_memberships)
    monkeypatch.setattr("app.models.managers.tenant_manager.TenantUserManager.member_grants", member_grants)


class _Recorder:
    """A `send` callable that records the response the middleware emits."""

    def __init__(self):
        self.messages: list[dict] = []

    async def __call__(self, message):
        self.messages.append(message)

    @property
    def status(self) -> int | None:
        for message in self.messages:
            if message.get("type") == "http.response.start":
                return message["status"]
        return None


async def _run(monkeypatch, *, user, rows=(), path="/admin/source/list", tenant_id=None):
    """Drive one console request; returns `(scope seen, capabilities, recorder)`."""
    probe = _Probe()

    async def resolve(request):
        return user

    monkeypatch.setattr(AdminScopeMiddleware, "_session_user", staticmethod(resolve))
    _memberships(monkeypatch, rows)
    recorder = _Recorder()
    await AdminScopeMiddleware(probe)(_request(path, tenant_id), None, recorder)
    return probe.seen, probe.capabilities, recorder


async def _scope(monkeypatch, **kwargs):
    seen, _, _ = await _run(monkeypatch, **kwargs)
    return seen


async def _decision(monkeypatch, **kwargs):
    """`(scope, capabilities)` of one request — the scope and the rights it grants."""
    seen, capabilities, _ = await _run(monkeypatch, **kwargs)
    return seen, capabilities


@pytest.mark.asyncio
async def test_the_platform_owner_keeps_global_workspace_inspection(monkeypatch):
    """The owner still reaches every workspace row, and their own catalogue only."""
    seen, capabilities = await _decision(monkeypatch, user=_user(2, superuser=True))
    assert seen == {"bypass": True, "tenant": None, "tenants": (), "actor": 2, "catalog_owner": 2}
    # The bypass is workspace data access; it never widens the personal catalogue,
    # so the bound owner is the actor's own id — not "everybody".
    assert seen["catalog_owner"] == seen["actor"] == 2
    assert capabilities == {"can_view_source": True, "can_update_source": True}


@pytest.mark.asyncio
async def test_an_invited_member_runs_in_the_workspace_that_invited_them(monkeypatch):
    """user2 invited by user1 as that workspace's admin: in, under that role."""
    grants = frozenset({("source", "view"), ("source", "update")})
    seen = await _scope(monkeypatch, user=_user(7), rows=[(3, grants)])
    assert seen["bypass"] is False
    assert seen["tenant"] == 3 and seen["tenants"] == (3,)
    # The personal catalogue is the actor's own, so the provider-key owner check
    # has somebody to compare against instead of raising for every operator.
    assert seen["actor"] == 7 and seen["catalog_owner"] == 7


@pytest.mark.asyncio
async def test_selected_workspace_is_validated_against_memberships(monkeypatch):
    """A strong membership cannot lend its role to a selected weak workspace.

    The stronger membership comes first, so a fixed "the first membership is
    active" rule would silently run the request under `source.update` while the
    operator believes they are in the workspace where they may only view.
    """
    strong = frozenset({("source", "view"), ("source", "update")})
    weak = frozenset({("source", "view")})
    seen, capabilities = await _decision(monkeypatch, user=_user(7), rows=[(3, strong), (9, weak)], tenant_id=9)
    assert seen["tenant"] == 9 and seen["tenants"] == (9,)
    # The selected (weaker) membership, not the stronger first one, is the role
    # the request runs under.
    assert capabilities == {"can_view_source": True, "can_update_source": False}


@pytest.mark.asyncio
async def test_an_invalid_workspace_selection_falls_back_to_a_real_membership(monkeypatch):
    """A stale `app_tenant_id` never becomes an unverified workspace."""
    strong = frozenset({("source", "view"), ("source", "update")})
    weak = frozenset({("source", "view")})
    for stale in (404, "not-a-number", None):
        seen, capabilities = await _decision(monkeypatch, user=_user(7), rows=[(3, strong), (9, weak)], tenant_id=stale)
        # Back to the oldest *verified* membership — never the typed value.
        assert seen["tenant"] == 3 and seen["tenants"] == (3,), stale
        assert capabilities["can_update_source"] is True, stale
    # A stranger's workspace id is exactly the same case as a missing one.
    seen, _ = await _decision(monkeypatch, user=_user(7), rows=[(3, strong), (9, weak)], tenant_id=10**9)
    assert seen["tenant"] == 3


@pytest.mark.asyncio
async def test_a_workspace_page_without_a_membership_is_a_refusal(monkeypatch):
    """Fail closed: no membership means no workspace page, not "the first one".

    `/admin/source/list` lists a workspace model, so an operator belonging to no
    workspace is refused up front (403, the same answer `/api` gives) instead of
    being run against an empty tenant context and crashing the manager.
    """
    seen, _, recorder = await _run(monkeypatch, user=_user(7), path="/admin/source/list")
    assert recorder.status == 403
    # The request never reached the view: nothing was read under the wrong scope.
    assert seen == {}


@pytest.mark.asyncio
async def test_a_personal_catalog_page_still_works_without_a_workspace(monkeypatch):
    """Providers/models are personal, not workspace rows: the actor's own catalogue."""
    seen, _, recorder = await _run(monkeypatch, user=_user(7), path="/admin/llm-provider/list")
    assert recorder.status is None
    assert seen["bypass"] is False
    assert seen["tenant"] is None and seen["tenants"] == ()
    assert seen["catalog_owner"] == 7


@pytest.mark.asyncio
async def test_the_sign_in_page_needs_no_actor_and_no_bypass(monkeypatch):
    seen = await _scope(monkeypatch, user=None, path="/admin/login")
    assert seen == {"bypass": False, "tenant": None, "tenants": (), "actor": None, "catalog_owner": None}


@pytest.mark.parametrize(
    "path,workspace",
    [
        ("/admin/source/list", True),
        ("/admin/agent-task/list", True),
        ("/admin/tenant/list", False),  # the workspace table itself, not rows in one
        ("/admin/llm-provider/list", False),
        ("/admin/llm-model/list", False),
        ("/admin/registry", True),  # every table: the owner only
        ("/admin/nothing-of-ours/list", True),  # unknown: fail closed
    ],
)
def test_workspace_pages_are_derived_from_the_model(path, workspace):
    """The classification comes from the model, not from a hand-kept list.

    It must also work before `setup_admin()` has run: the previous version read
    the *instance* registry, so in a fresh process every page — the personal
    catalogues included — was refused as workspace data.
    """
    assert AdminScopeMiddleware._reads_workspace_data(path) is workspace


@pytest.mark.asyncio
async def test_an_unreadable_membership_list_grants_no_workspace(monkeypatch):
    """A database failure must not fall back to "show everything"."""

    async def broken(self, user_id):
        raise RuntimeError("database is down")

    monkeypatch.setattr("app.models.managers.tenant_manager.TenantUserManager.web_memberships", broken)

    async def resolve(request):
        return _user(7)

    monkeypatch.setattr(AdminScopeMiddleware, "_session_user", staticmethod(resolve))
    recorder = _Recorder()
    await AdminScopeMiddleware(_Probe())(_request(), None, recorder)
    assert recorder.status == 403


@pytest.mark.asyncio
async def test_the_middleware_leaves_every_other_surface_alone():
    """`/app`, `/api` and `/static` own their own scope; this one only claims `/admin`."""
    called = []

    async def app(scope, receive, send):
        called.append(scope["path"])

    middleware = AdminScopeMiddleware(app)
    for path in ("/app/settings", "/api/v1/ping", "/static/logo.png", "/administrator-old"):
        await middleware(_request(path), None, None)
    assert called == ["/app/settings", "/api/v1/ping", "/static/logo.png", "/administrator-old"]


def test_the_platform_middleware_no_longer_owns_the_console():
    """`/admin` is excluded, so the two middleware cannot fight over the bypass."""
    assert "/admin" in PlatformScopeMiddleware.TENANT_SCOPED_PREFIXES
    assert "/app" in PlatformScopeMiddleware.TENANT_SCOPED_PREFIXES


def test_the_console_scope_wins_over_the_platform_bypass():
    """Whatever runs outside the console must not decide the console's scope.

    `AdminScopeMiddleware` is added last, so Starlette wraps it *outermost*: the
    platform bypass opens first and the console scope narrows it for `/admin/*`.
    On `/admin` the console therefore has the last word, not the bypass.
    """
    source = (ROOT / "app/main.py").read_text()
    assert source.index("add_middleware(PlatformScopeMiddleware)") < source.index(
        "add_middleware(AdminScopeMiddleware)"
    )


@pytest.mark.asyncio
async def test_the_console_narrows_the_platform_bypass(monkeypatch):
    """Run inside the real stack's outer bypass: `/admin` must still come out scoped.

    `add_middleware` order is the reverse of the call order, so the console
    middleware sits outside `PlatformScopeMiddleware` and sees `bypass=True` on
    entry. It must replace that scope, never inherit it.
    """
    seen = {}

    async def app(scope, receive, send):
        seen["bypass"] = is_bypass()
        seen["tenant"] = current_tenant_id()

    async def resolve(request):
        return _user(7)

    monkeypatch.setattr(AdminScopeMiddleware, "_session_user", staticmethod(resolve))
    _memberships(monkeypatch, [(3, frozenset({("source", "view")}))])
    console = AdminScopeMiddleware(app)
    # The outer platform middleware would have opened a bypass around this call.
    with tenant_scope(bypass=True):
        await console(_request("/admin/source/list"), None, _Recorder())
    assert seen["bypass"] is False
    assert seen["tenant"] == 3
