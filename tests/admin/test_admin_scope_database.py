"""What a console actor is allowed to see, once the scope is bound.

`AdminScopeMiddleware` decides the ambient scope; these tests pin what the
managers and the permission checks then do with it, against real rows:

- the platform bypass never widens a personal catalog beyond its bound owner —
  for the platform owner (`superuser`) as for anyone else;
- every console actor reads only their own personal rows (`owner_user_id`) and
  the one workspace selected for the request, with the role of *that* membership
  as the right source — a stronger membership held elsewhere lends nothing;
- a direct URL to a row outside that scope is a 403, for reads and writes alike;
- no table under the console is left ungated: a workspace model without the
  scope is an error, not an unfiltered listing.

Test data is arranged with *trusted setup* scopes only: `tenant_scope(bypass=True)`
plus the explicit owner of a personal row, exactly like the bootstrap/CLI paths.
Those writes are not assertions; the request scope is always the real one.

These are `tenancy` tests on purpose: the autouse fixture switches the bypass
off so that "no scope" is the failure mode, not the default.
"""

import secrets
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncIterator

import pytest
from httpx import AsyncClient

from app.core.access_catalog import workspace_grants
from app.core.permissions import has_permission, operator_permission_scope, permission_scope
from app.core.personal_catalog_context import catalog_execution_scope
from app.core.tenant_context import (
    TenantContextError,
    current_tenant_ids,
    tenant_scope,
)
from app.models import AgentTask, LLMModel, LLMProvider, PlatformRole, Source, Tenant, TenantUser, User
from app.types import ActionType, PlatformRoleType

pytestmark = pytest.mark.tenancy


@contextmanager
def setup_scope(owner: User | None = None):
    """Trusted arrange scope: platform data access plus an explicit personal owner.

    This is the bootstrap/CLI shape (`tenant_scope(bypass=True)` and the named
    catalogue owner), never the shape of an HTTP request. Assertions run outside
    it, on the real request scope — a fixture that arranged its rows through the
    very bypass under test would prove nothing about it.
    """
    with tenant_scope(bypass=True), operator_permission_scope():
        if owner is None:
            yield
        else:
            with catalog_execution_scope(owner.id):
                yield


def _uniq(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(5)}"


async def _user(prefix: str, role: PlatformRoleType = PlatformRoleType.USER) -> User:
    with setup_scope():
        role_row = await PlatformRole.objects.get(codename=role.value)
        name = _uniq(prefix)
        return await User.objects.create_user(
            username=name,
            email=f"{name}@example.com",
            password="scope-password-1",
            role_id=role_row.id,
        )


async def _workspace() -> Tenant:
    with setup_scope():
        slug = _uniq("scope-ws")
        return await Tenant.objects.create(name=slug, slug=slug)


async def _member(tenant: Tenant, user: User, role_codename: str = "admin") -> TenantUser:
    from app.models import WorkspaceRole

    with setup_scope():
        role = await WorkspaceRole.objects.get(codename=role_codename)
        return await TenantUser.objects.create(
            tenant_id=tenant.id,
            user_id=user.id,
            channel="web",
            external_user_id=str(user.id),
            role_id=role.id,
            is_active=True,
        )


async def _provider(owner: User, name: str) -> LLMProvider:
    """A provider in `owner`'s catalogue; the write needs that owner bound."""
    with setup_scope(owner):
        return await LLMProvider.objects.create(
            uid=_uniq("prov"),
            name=name,
            api_format="openai",
            base_url="https://example.invalid/v1",
            is_active=True,
        )


async def _membership_scope(user: User) -> list[tuple[int, frozenset]]:
    """What `AdminScopeMiddleware` resolves for `user` (same query, no HTTP)."""
    from app.models.managers.tenant_manager import TenantUserManager

    manager = TenantUserManager()
    memberships = await manager.web_memberships(user.id)
    return [(m.tenant_id, await manager.member_grants(m)) for m in memberships]


def _selected(rows: list[tuple[int, frozenset]], active_tenant_id: int | None = None) -> tuple[int, frozenset]:
    """The one membership a request runs as: the selected one, else the oldest."""
    return next((row for row in rows if row[0] == active_tenant_id), rows[0])


def _scope(rows: list[tuple[int, frozenset]], active_tenant_id: int | None = None):
    """One selected membership controls each request and manager operation."""
    return tenant_scope(_selected(rows, active_tenant_id)[0])


# --- personal rows --------------------------------------------------------


@pytest.mark.asyncio
async def test_a_personal_catalog_is_only_its_owner_s_rows():
    """Two operators, two providers: neither may read across the `owner_user_id`."""
    first, second = await _user("scope-a"), await _user("scope-b")
    mine = await _provider(first, "mine")
    theirs = await _provider(second, "theirs")

    with catalog_execution_scope(first.id), permission_scope(first):
        visible = await LLMProvider.objects.all()
        assert [row.id for row in visible] == [mine.id]
        assert await LLMProvider.objects.get(id=theirs.id) is None


@pytest.mark.asyncio
async def test_the_platform_owner_reads_only_their_own_personal_catalog():
    """`superuser` keeps workspace inspection; the personal catalog stays owner-bound.

    The bypass is data access inside the console, so a personal row is still
    filtered by its `owner_user_id`: the owner sees their catalogue, not every
    user's providers. A platform grant never turns a catalogue global.
    """
    owner = await _user("scope-owner", PlatformRoleType.SUPERUSER)
    first, second = await _user("scope-c"), await _user("scope-d")
    mine = await _provider(owner, "owner-visible-1")
    await _provider(first, "other-user-1")
    await _provider(second, "other-user-2")

    with tenant_scope(bypass=True), catalog_execution_scope(owner.id), permission_scope(owner):
        ids = {row.id for row in await LLMProvider.objects.all()}
        assert ids == {mine.id}
        assert await LLMProvider.objects.get(id=mine.id) is not None


@pytest.mark.asyncio
async def test_a_foreign_provider_key_is_refused_inside_the_owner_scope():
    """A direct URL to someone else's provider cannot read its key — the 403 path."""
    first, second = await _user("scope-e"), await _user("scope-f")
    foreign = await _provider(second, "foreign")

    with catalog_execution_scope(first.id):
        with pytest.raises(PermissionError):
            foreign.get_api_key()


@pytest.mark.asyncio
async def test_a_console_model_write_cannot_pick_an_owner():
    """The console writes the actor's catalogue; a form's `owner_user_id` is refused."""
    actor, other = await _user("scope-g"), await _user("scope-h")
    provider = await _provider(actor, "own-provider")
    with permission_scope(actor), catalog_execution_scope(actor.id):
        created = await LLMModel.objects.create(
            name=_uniq("model"),
            model_id=_uniq("model-id"),
            provider_id=provider.id,
        )
        with pytest.raises(PermissionError):
            await LLMModel.objects.create(
                name=_uniq("model"),
                model_id=_uniq("model-id"),
                provider_id=provider.id,
                owner_user_id=other.id,
            )
    assert created.owner_user_id == actor.id


# --- workspace rows -------------------------------------------------------


@pytest.mark.asyncio
async def test_an_invited_member_reaches_the_inviting_workspace_selected_for_the_request():
    """user2 keeps their own workspace and reads user1's as its invited admin.

    Both workspaces are *their* memberships; the request runs in exactly one of
    them, chosen per request — the invited workspace here.
    """
    owner, helper = await _user("scope-i"), await _user("scope-j")
    home, invited = await _workspace(), await _workspace()
    await _member(home, helper, "owner")
    await _member(invited, helper, "admin")
    await _member(invited, owner, "owner")
    with setup_scope():
        await Source.objects.create(
            tenant_id=invited.id, name=_uniq("src"), platform_id=1, source_type="GROUP", external_id=_uniq("ext")
        )

    rows = await _membership_scope(helper)
    assert {tenant_id for tenant_id, _ in rows} == {home.id, invited.id}
    selected_id, selected_grants = _selected(rows, invited.id)
    assert selected_id == invited.id
    with tenant_scope(selected_id), permission_scope(helper, member_grants=selected_grants):
        visible = await Source.objects.filter(tenant_id=invited.id)
        # `source.view` belongs to the invited admin role; `update` too.
        assert has_permission(helper, "source", "update")
    assert visible, "the invited workspace's sources are readable by the invited member"


@pytest.mark.asyncio
async def test_a_selected_membership_cannot_borrow_a_stronger_one_s_rights():
    """Strong first, weak second: the selected weak workspace decides the rights.

    The manager reads rows of the selected workspace only, and the role that
    authorizes an action is that membership's (`viewer`: read, no update) — the
    earlier `owner` membership in the same actor's list lends nothing. The console
    backend applies exactly this `has_permission` check before it grants an
    action, so the refusal here is the same refusal that hides the edit form.
    """
    helper = await _user("scope-strong-first")
    strong, weak = await _workspace(), await _workspace()
    await _member(strong, helper, "owner")
    await _member(weak, helper, "viewer")
    with setup_scope():
        weak_source = await Source.objects.create(
            tenant_id=weak.id, name=_uniq("weak"), platform_id=1, source_type="GROUP", external_id=_uniq("weak")
        )
        strong_source = await Source.objects.create(
            tenant_id=strong.id, name=_uniq("strong"), platform_id=1, source_type="GROUP", external_id=_uniq("strong")
        )

    rows = await _membership_scope(helper)
    assert [tenant_id for tenant_id, _ in rows][:2] == [strong.id, weak.id]
    selected_id, selected_grants = _selected(rows, weak.id)
    assert selected_id == weak.id
    with tenant_scope(selected_id), permission_scope(helper, member_grants=selected_grants):
        assert has_permission(helper, "source", "view")
        assert not has_permission(helper, "source", "update")
        # Only the selected workspace is readable, although the actor owns the other.
        assert [row.id for row in await Source.objects.all()] == [weak_source.id]
        assert await Source.objects.get(id=strong_source.id) is None

    # The stronger `owner` membership is not reachable in this request either: the
    # same helper inside it *does* grant update, proving the difference is the
    # selected membership and nothing else.
    owner_id, owner_grants = _selected(rows, strong.id)
    with tenant_scope(owner_id), permission_scope(helper, member_grants=owner_grants):
        assert has_permission(helper, "source", "update")


@pytest.mark.asyncio
async def test_a_member_of_one_workspace_cannot_read_another():
    """Membership is the boundary: a workspace the actor is not in yields nothing."""
    outsider = await _user("scope-k")
    foreign_ws = await _workspace()
    await _member(foreign_ws, outsider, "admin")

    stranger_ws = await _workspace()
    rows = await _membership_scope(outsider)
    with _scope(rows):
        assert current_tenant_ids() == (foreign_ws.id,)
        assert await Source.objects.filter(tenant_id=stranger_ws.id) == []


@pytest.mark.asyncio
async def test_a_workspace_row_without_a_scope_is_an_error_not_a_listing():
    """A view that somehow runs outside the console scope must fail closed."""
    with pytest.raises(TenantContextError):
        await Source.objects.all()


@pytest.mark.asyncio
async def test_an_unaffiliated_operator_cannot_open_a_workspace_task():
    """`/admin/agent-task/edit/<id>` of somebody else's workspace resolves to nothing."""
    worker = await _user("scope-l")
    workspace = await _workspace()
    await _member(workspace, worker, "owner")
    scenario_id = await _scenario_id(workspace.id)
    with setup_scope():
        task = await AgentTask.objects.create(
            tenant_id=workspace.id,
            name=_uniq("task"),
            cron_expr="0 9 * * *",
            job_type="collect",
            agent_scenario_id=scenario_id,
        )

    other = await _workspace()
    with tenant_scope(workspace.id):
        assert (await AgentTask.objects.all()) != []
        assert await AgentTask.objects.get(id=task.id) is not None
    with tenant_scope(other.id):
        assert await AgentTask.objects.all() == []
        assert await AgentTask.objects.get(id=task.id) is None


# --- rights ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_invited_membership_role_is_what_grants_the_console_right():
    """user1 invites user2 as admin: user2 may configure user1's workspace, in admin too."""
    helper = await _user("scope-m")
    workspace = await _workspace()
    await _member(workspace, helper, "admin")
    with setup_scope():
        await Source.objects.create(
            tenant_id=workspace.id, name=_uniq("src"), platform_id=1, source_type="GROUP", external_id=_uniq("ext")
        )

    rows = await _membership_scope(helper)
    selected_id, grants = _selected(rows)
    assert selected_id == workspace.id
    with tenant_scope(selected_id), permission_scope(helper, member_grants=grants):
        assert has_permission(helper, "source", "update")
        assert has_permission(helper, "agenttask", "create")
        # The same membership role decides what the row writes may do.
        source = (await Source.objects.all())[0]
        updated = await Source.objects.update_by_id(source.id, name=_uniq("renamed"))
        assert updated.name.startswith("renamed")
    # A weaker membership role keeps only what its preset lists. `workspace_grants`
    # is the single source of those presets; a hand-made grant set is not a role.
    viewer_grants = workspace_grants("viewer")
    assert ("source", ActionType.VIEW) in viewer_grants
    with _scope(rows), permission_scope(helper, member_grants=viewer_grants):
        assert has_permission(helper, "source", "view")
        assert not has_permission(helper, "source", "update")


@pytest.mark.asyncio
async def test_a_membership_role_never_unlocks_the_personal_catalog():
    """Being admin of someone's workspace does not make their provider key usable."""
    owner, helper = await _user("scope-n"), await _user("scope-o")
    workspace = await _workspace()
    await _member(workspace, helper, "admin")
    await _member(workspace, owner, "owner")
    foreign = await _provider(owner, "workspace-owner-provider")

    rows = await _membership_scope(helper)
    with _scope(rows), catalog_execution_scope(helper.id), permission_scope(helper, member_grants=rows[0][1]):
        assert await LLMProvider.objects.get(id=foreign.id) is None
    # And the right is not handed to the *client* level: `has_permission` reads
    # the row's platform grants, and a plain `user` account carries none.
    assert not has_permission(_client(helper), "llmprovider", "update")
    # The row itself is intact and still readable by its owner.
    with catalog_execution_scope(owner.id):
        assert (await LLMProvider.objects.get(id=foreign.id)).id == foreign.id


# --- helpers --------------------------------------------------------------


def _client(user):
    """A view of `user` carrying the client level's role instead of its real one."""
    from types import SimpleNamespace

    return SimpleNamespace(
        id=user.id,
        is_active=True,
        role=SimpleNamespace(codename=PlatformRoleType.USER, permissions=[], label="Пользователь"),
    )


async def _scenario_id(tenant_id: int) -> int:
    """A scenario inside `tenant_id`; the task manager binds one per workspace."""
    from app.models import AgentScenario

    with tenant_scope(bypass=True):
        return (await AgentScenario.objects.create(name=_uniq("scenario"), tenant_id=tenant_id)).id


# --- HTTP regressions -----------------------------------------------------
#
# Real ASGI requests against the registered console: the middleware, the
# managers, the authorization backend and SQLAdmin's own session all run. The
# fixtures live in this run's uniquely owned schema; nothing is sent to a
# provider and no write leaves the schema.

PASSWORD = "scope-password-1"


@asynccontextmanager
async def _console(user: User, tenant_id: int | None = None) -> AsyncIterator[AsyncClient]:
    """A real `/admin` login whose session carries an explicit workspace.

    `app_tenant_id` is written into the signed session cookie and nothing else:
    it selects among the actor's own memberships, which the middleware
    re-validates, so a forged value can only fall back.
    """
    import base64
    import json
    import re

    from httpx import ASGITransport, AsyncClient
    from starlette.middleware.sessions import SessionMiddleware

    from app.core.config import settings
    from app.main import create_application

    app = create_application()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        page = await client.get("/admin/login")
        token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text).group(1)
        await client.post(
            "/admin/login",
            data={"username": user.username, "password": PASSWORD, "csrf_token": token},
            follow_redirects=True,
        )
        assert client.cookies.get("session"), "The console login did not issue a session"
        if tenant_id is not None:
            signer = SessionMiddleware(app, secret_key=settings.SECRET_KEY).signer
            raw = client.cookies.get("session").encode("utf-8")
            payload = json.loads(base64.b64decode(signer.unsign(raw)))
            payload["app_tenant_id"] = tenant_id
            client.cookies.set("session", signer.sign(base64.b64encode(json.dumps(payload).encode())).decode())
        yield client


@asynccontextmanager
async def _admin_grants(*resource_keys: str) -> AsyncIterator[None]:
    """Give the platform `admin` role these resources' grants; restore all in `finally`."""
    from app.models import Permission, Role

    role = await Role.objects.get(codename=PlatformRoleType.ADMIN.value)
    loaded = await Role.objects.get_with_permissions(role.id)
    original = [permission.id for permission in loaded.permissions]
    catalog = await Permission.objects.prefetch_related("model_type").all()
    selected = [p.id for p in catalog if p.model_type and p.model_type.resource_key in set(resource_keys)]
    with setup_scope():
        await Role.objects.set_permissions(role.id, selected)
    try:
        yield
    finally:
        with setup_scope():
            await Role.objects.set_permissions(role.id, original)
        with setup_scope():
            restored = await Role.objects.get_with_permissions(role.id)
        assert sorted(p.id for p in restored.permissions) == sorted(original)


@pytest.mark.asyncio
async def test_admin_http_uses_selected_weak_membership_with_stronger_membership_first():
    """The strong-first membership cannot lend its rights to the selected viewer workspace.

    The platform `admin` role is given `source` grants (the outer ceiling).
    Inside the selected `viewer` workspace, that ceiling is capped: the list and
    details pages open, the edit form and row are refused, and an unselected
    workspace id — even one the same actor administers — is not there at all.
    """
    actor = await _user("scope-http", PlatformRoleType.ADMIN)
    strong_workspace, weak_workspace = await _workspace(), await _workspace()
    await _member(strong_workspace, actor, "admin")
    await _member(weak_workspace, actor, "viewer")
    with setup_scope():
        weak_source = await Source.objects.create(
            tenant_id=weak_workspace.id,
            name=_uniq("selected-viewer"),
            platform_id=1,
            source_type="GROUP",
            external_id=_uniq("selected"),
        )
        strong_source = await Source.objects.create(
            tenant_id=strong_workspace.id,
            name=_uniq("unselected-admin"),
            platform_id=1,
            source_type="GROUP",
            external_id=_uniq("unselected"),
        )

    async with _admin_grants("collection.source"):
        async with _console(actor, weak_workspace.id) as client:
            listing = await client.get("/admin/source/list")
            assert listing.status_code == 200, "The viewer membership still reads the list"
            assert weak_source.name in listing.text
            assert strong_source.name not in listing.text
            assert (await client.get(f"/admin/source/details/{weak_source.id}")).status_code == 200
            assert (await client.get(f"/admin/source/edit/{weak_source.id}")).status_code == 403
            # A workspace the actor administers but did not select is not in this request.
            assert (await client.get(f"/admin/source/details/{strong_source.id}")).status_code == 404
            assert (await client.get(f"/admin/source/edit/{strong_source.id}")).status_code == 404

    # The unselected workspace is reachable only when it is selected — with its
    # own role, not by reusing the viewer request's permissions.
    async with _admin_grants("collection.source"):
        async with _console(actor, strong_workspace.id) as client:
            assert (await client.get(f"/admin/source/edit/{strong_source.id}")).status_code == 200
            assert (await client.get(f"/admin/source/details/{weak_source.id}")).status_code == 404


@pytest.mark.asyncio
async def test_admin_http_invalid_workspace_falls_back_to_a_verified_membership():
    """A forged/foreign `app_tenant_id` selects nothing: the oldest membership runs."""
    actor = await _user("scope-http-fallback", PlatformRoleType.ADMIN)
    first, second = await _workspace(), await _workspace()
    await _member(first, actor, "admin")
    await _member(second, actor, "viewer")
    stranger = await _workspace()
    with setup_scope():
        first_source = await Source.objects.create(
            tenant_id=first.id,
            name=_uniq("first-admin"),
            platform_id=1,
            source_type="GROUP",
            external_id=_uniq("first"),
        )
        second_source = await Source.objects.create(
            tenant_id=second.id,
            name=_uniq("second-viewer"),
            platform_id=1,
            source_type="GROUP",
            external_id=_uniq("second"),
        )

    async with _admin_grants("collection.source"):
        async with _console(actor, stranger.id) as client:
            listing = await client.get("/admin/source/list")
            assert listing.status_code == 200
            # The oldest verified membership (admin of `first`), never the stranger.
            assert first_source.name in listing.text
            assert second_source.name not in listing.text
            assert (await client.get(f"/admin/source/details/{first_source.id}")).status_code == 200
            assert (await client.get(f"/admin/source/details/{second_source.id}")).status_code == 404


@pytest.mark.asyncio
async def test_admin_http_personal_catalog_blocks_foreign_provider_and_model_routes():
    """Personal ownership protects list, details, edit, POST and delete in SQLAdmin.

    The actor may read/write *their own* catalogue (the platform grants cover
    `llm.provider`/`llm.model`); another user's rows vanish from the list, and a
    direct details/edit URL, a forged edit POST or a delete request cannot even
    find them. The foreign rows stay intact and readable by their owner.
    """
    actor = await _user("scope-owner-http", PlatformRoleType.ADMIN)
    other = await _user("scope-foreign-http")
    own_provider = await _provider(actor, "own-http-provider")
    foreign_provider = await _provider(other, "foreign-http-provider")
    with setup_scope(actor):
        own_model = await LLMModel.objects.create(
            name=_uniq("own-model"), model_id=_uniq("own-model-id"), provider_id=own_provider.id
        )
    with setup_scope(other):
        foreign_model = await LLMModel.objects.create(
            name=_uniq("foreign-model"), model_id=_uniq("foreign-model-id"), provider_id=foreign_provider.id
        )

    async with _admin_grants("llm.provider", "llm.model"):
        async with _console(actor) as client:
            own_list = await client.get("/admin/llm-provider/list")
            assert own_list.status_code == 200 and own_provider.name in own_list.text
            assert foreign_provider.name not in own_list.text
            model_list = await client.get("/admin/llm-model/list")
            assert model_list.status_code == 200 and own_model.name in model_list.text
            assert foreign_model.name not in model_list.text

            assert (await client.get("/admin/llm-provider/create")).status_code == 200
            assert (await client.get("/admin/llm-model/create")).status_code == 200
            assert (await client.get(f"/admin/llm-provider/edit/{own_provider.id}")).status_code == 200
            assert (await client.get(f"/admin/llm-model/edit/{own_model.id}")).status_code == 200

            for identity, record in (("llm-provider", foreign_provider), ("llm-model", foreign_model)):
                assert (await client.get(f"/admin/{identity}/details/{record.id}")).status_code in {403, 404}
                assert (await client.get(f"/admin/{identity}/edit/{record.id}")).status_code in {403, 404}
                forged = await client.post(
                    f"/admin/{identity}/edit/{record.id}",
                    data={"name": "forged-by-http", "csrf_token": _valid_csrf_token()},
                )
                assert forged.status_code in {403, 404}, identity
                deleted = await client.post(
                    f"/admin/{identity}/delete?pks={record.id}", data={"csrf_token": _valid_csrf_token()}
                )
                assert deleted.status_code in {403, 404}, identity

    # The foreign rows are still there, unchanged, and still theirs.
    with setup_scope(other):
        assert (await LLMProvider.objects.get(id=foreign_provider.id)).name == foreign_provider.name
        assert (await LLMModel.objects.get(id=foreign_model.id)).name == foreign_model.name
    with setup_scope(actor):
        assert (await LLMProvider.objects.get(id=own_provider.id)).name == own_provider.name


def _valid_csrf_token() -> str:
    """A signed, well-formed token: the refusal must not be "bad CSRF"."""
    from app.admin.csrf import CSRFTokenManager
    from app.core.config import settings

    return CSRFTokenManager(secret_key=settings.SECRET_KEY).generate_token()
