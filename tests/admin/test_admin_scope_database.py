"""What a console actor is allowed to see, once the scope is bound.

`AdminScopeMiddleware` decides the ambient scope; these tests pin what the
managers and the permission checks then do with it, against real rows:

- the platform bypass does not widen a personal catalog beyond its bound owner;
- everyone else reads only their own personal rows (`owner_user_id`) and the
  workspaces they are a member of, with that membership's role as the right
  source — the same rule the web UI applies to an invited member;
- a direct URL to a row outside that scope is a 403, for reads and writes alike;
- no table under the console is left ungated: a workspace model without the
  scope is an error, not an unfiltered listing.

These are `tenancy` tests on purpose: the autouse fixture switches the bypass
off so that "no scope" is the failure mode, not the default.
"""

import secrets

import pytest
from sqlalchemy import select

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


def _uniq(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(5)}"


async def _user(prefix: str, role: PlatformRoleType = PlatformRoleType.USER) -> User:
    role_row = await PlatformRole.objects.get(codename=role.value)
    name = _uniq(prefix)
    return await User.objects.create_user(
        username=name,
        email=f"{name}@example.com",
        password="scope-password-1",
        role_id=role_row.id,
    )


async def _workspace() -> Tenant:
    slug = _uniq("scope-ws")
    return await Tenant.objects.create(name=slug, slug=slug)


async def _member(tenant: Tenant, user: User, role_codename: str = "admin") -> TenantUser:
    from app.models import WorkspaceRole

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
    with permission_scope(owner), catalog_execution_scope(owner.id):
        return await LLMProvider.objects.create(
            uid=_uniq("prov"),
            name=name,
            api_format="openai",
            base_url="https://example.invalid/v1",
            is_active=True,
        )


async def _crud(manager, action: str, *, user, session=None, **values):
    """Run a manager write the way the console does: actor + their scope.

    The console has no workspace-grant path today: its write rights come from
    the actor's platform grants (`AdminAuthorizationBackend`), so this helper is
    the only writer that needs `operator_permission_scope()`.
    """
    method = getattr(manager, action)
    with permission_scope(user), operator_permission_scope():
        if session is not None:
            values["session"] = session
        return await method(**values)


async def _membership_scope(user: User) -> list[tuple[int, frozenset]]:
    """What `AdminScopeMiddleware` resolves for `user` (same query, no HTTP)."""
    from app.models.managers.tenant_manager import TenantUserManager

    manager = TenantUserManager()
    memberships = await manager.web_memberships(user.id)
    return [(m.tenant_id, await manager.member_grants(m)) for m in memberships]


def _scope(rows: list[tuple[int, frozenset]], active_tenant_id: int | None = None):
    """One selected membership controls each request and manager operation."""
    active = next((row for row in rows if row[0] == active_tenant_id), rows[0])
    return tenant_scope(active[0])


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
async def test_platform_bypass_does_not_widen_the_bound_personal_catalog():
    """The platform bypass still keeps catalog reads bound to its owner."""
    first, second = await _user("scope-c"), await _user("scope-d")
    mine = await _provider(first, "owner-visible-1")
    theirs = await _provider(second, "owner-visible-2")

    with tenant_scope(bypass=True), catalog_execution_scope(first.id):
        ids = {row.id for row in await LLMProvider.objects.all()}
    assert mine.id in ids
    assert theirs.id not in ids


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
async def test_an_invited_member_reads_the_workspace_that_invited_them():
    """user2 keeps their own workspace and reads user1's as its invited admin."""
    owner, helper = await _user("scope-i"), await _user("scope-j")
    home, invited = await _workspace(), await _workspace()
    await _member(home, helper, "owner")
    await _member(invited, helper, "admin")
    await _member(invited, owner, "owner")
    with tenant_scope(bypass=True):
        await Source.objects.create(
            tenant_id=invited.id, name=_uniq("src"), platform_id=1, source_type="GROUP", external_id=_uniq("ext")
        )

    rows = await _membership_scope(helper)
    assert {tenant_id for tenant_id, _ in rows} == {home.id, invited.id}
    with _scope(rows, invited.id), permission_scope(helper, member_grants=next(g for tid, g in rows if tid == invited.id)):
        visible = await Source.objects.filter(tenant_id=invited.id)
        # `source.view` belongs to the invited admin role; `update` too.
        assert has_permission(helper, "source", "update")
    assert visible, "the invited workspace's sources are readable by the invited member"


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
    with tenant_scope(bypass=True):
        await AgentTask.objects.create(
            tenant_id=workspace.id,
            name=_uniq("task"),
            cron_expr="0 9 * * *",
            job_type="collect",
            agent_scenario_id=await _scenario_id(workspace.id),
        )

    other = await _workspace()
    with tenant_scope(workspace.id):
        assert (await AgentTask.objects.all()) != []
    with tenant_scope(other.id):
        assert await AgentTask.objects.all() == []


# --- rights ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_invited_membership_role_is_what_grants_the_console_right():
    """user1 invites user2 as admin: user2 may configure user1's workspace, in admin too."""
    helper = await _user("scope-m")
    workspace = await _workspace()
    await _member(workspace, helper, "admin")
    with tenant_scope(bypass=True):
        await Source.objects.create(
            tenant_id=workspace.id, name=_uniq("src"), platform_id=1, source_type="GROUP", external_id=_uniq("ext")
        )

    rows = await _membership_scope(helper)
    active, grants = rows[0]
    with _scope(rows), permission_scope(helper, member_grants=grants):
        assert has_permission(helper, "source", "update")
        assert has_permission(helper, "agenttask", "create")
        # The same membership role decides what the row writes may do.
        with operator_permission_scope():
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
        return (
            await AgentScenario.objects.create(name=_uniq("scenario"), tenant_id=tenant_id)
        ).id





@pytest.mark.asyncio
async def test_admin_http_uses_selected_weak_membership_with_stronger_membership_first():
    """A stronger first workspace cannot lend its grants to the selected viewer workspace."""
    import base64
    import json
    import re
    from httpx import ASGITransport, AsyncClient
    from starlette.middleware.sessions import SessionMiddleware
    from app.core.config import settings
    from app.models import Permission, Role

    actor = await _user("scope-http", PlatformRoleType.ADMIN)
    strong_workspace, weak_workspace = await _workspace(), await _workspace()
    await _member(strong_workspace, actor, "admin")
    await _member(weak_workspace, actor, "viewer")
    with tenant_scope(bypass=True):
        weak_source = await Source.objects.create(
            tenant_id=weak_workspace.id, name=_uniq("selected-viewer"), platform_id=1,
            source_type="GROUP", external_id=_uniq("selected"),
        )
        strong_source = await Source.objects.create(
            tenant_id=strong_workspace.id, name=_uniq("unselected-admin"), platform_id=1,
            source_type="GROUP", external_id=_uniq("unselected"),
        )

    role = await Role.objects.get(codename=PlatformRoleType.ADMIN.value)
    loaded = await Role.objects.get_with_permissions(role.id)
    original = [permission.id for permission in loaded.permissions]
    permissions = await Permission.objects.prefetch_related("model_type").all()
    selected = [p.id for p in permissions if p.model_type and p.model_type.resource_key == "collection.source"]
    with tenant_scope(bypass=True):
        await Role.objects.set_permissions(role.id, selected)
    try:
        from app.main import create_application
        app = create_application()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            page = await client.get("/admin/login")
            token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text).group(1)
            await client.post("/admin/login", data={
                "username": actor.username, "password": "scope-password-1", "csrf_token": token,
            }, follow_redirects=True)
            raw = client.cookies.get("session")
            signer = SessionMiddleware(app, secret_key=settings.SECRET_KEY).signer
            payload = json.loads(base64.b64decode(signer.unsign(raw.encode("utf-8"))))
            payload["app_tenant_id"] = weak_workspace.id
            client.cookies.set("session", signer.sign(base64.b64encode(json.dumps(payload).encode())).decode())

            listing = await client.get("/admin/source/list")
            assert listing.status_code == 200
            assert weak_source.name in listing.text
            assert strong_source.name not in listing.text
            assert (await client.get(f"/admin/source/details/{weak_source.id}")).status_code == 200
            assert (await client.get(f"/admin/source/edit/{weak_source.id}")).status_code == 403
            assert (await client.get(f"/admin/source/details/{strong_source.id}")).status_code == 404
    finally:
        with tenant_scope(bypass=True):
            await Role.objects.set_permissions(role.id, original)


@pytest.mark.asyncio
async def test_admin_http_personal_catalog_blocks_foreign_provider_and_model_routes():
    """Personal ownership protects list, details, edit, POST and delete in SQLAdmin."""
    import re
    from httpx import ASGITransport, AsyncClient
    from app.core.config import settings
    from app.models import LLMModel, Permission, Role

    actor = await _user("scope-owner-http", PlatformRoleType.ADMIN)
    other = await _user("scope-foreign-http")
    own_provider = await _provider(actor, "own-http-provider")
    foreign_provider = await _provider(other, "foreign-http-provider")
    with permission_scope(actor), catalog_execution_scope(actor.id):
        own_model = await LLMModel.objects.create(
            name=_uniq("own-model"), model_id=_uniq("own-model-id"), provider_id=own_provider.id,
        )
    with permission_scope(other), catalog_execution_scope(other.id):
        foreign_model = await LLMModel.objects.create(
            name=_uniq("foreign-model"), model_id=_uniq("foreign-model-id"), provider_id=foreign_provider.id,
        )

    role = await Role.objects.get(codename=PlatformRoleType.ADMIN.value)
    loaded = await Role.objects.get_with_permissions(role.id)
    original = [permission.id for permission in loaded.permissions]
    permissions = await Permission.objects.prefetch_related("model_type").all()
    keys = {"llm.provider", "llm.model"}
    selected = [p.id for p in permissions if p.model_type and p.model_type.resource_key in keys]
    with tenant_scope(bypass=True):
        await Role.objects.set_permissions(role.id, selected)
    try:
        from app.main import create_application
        app = create_application()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            page = await client.get("/admin/login")
            login_token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text).group(1)
            await client.post("/admin/login", data={
                "username": actor.username, "password": "scope-password-1", "csrf_token": login_token,
            }, follow_redirects=True)
            own_list = await client.get("/admin/llm-provider/list")
            assert own_list.status_code == 200 and own_provider.name in own_list.text
            assert foreign_provider.name not in own_list.text
            assert (await client.get("/admin/llm-provider/create")).status_code == 200
            assert (await client.get(f"/admin/llm-provider/edit/{own_provider.id}")).status_code == 200
            assert (await client.get(f"/admin/llm-model/edit/{own_model.id}")).status_code == 200
            for identity, record in (("llm-provider", foreign_provider), ("llm-model", foreign_model)):
                assert (await client.get(f"/admin/{identity}/details/{record.id}")).status_code == 404
                assert (await client.get(f"/admin/{identity}/edit/{record.id}")).status_code == 404
                post = await client.post(
                    f"/admin/{identity}/edit/{record.id}", data={"name": "forged"},
                    headers={"X-CSRF-Token": app.state.csrf_manager.generate_token()},
                )
                assert post.status_code == 404
                await client.post(
                    f"/admin/{identity}/delete?pks={record.id}",
                    headers={"X-CSRF-Token": app.state.csrf_manager.generate_token()},
                )
            with permission_scope(other), catalog_execution_scope(other.id):
                assert await LLMProvider.objects.get(id=foreign_provider.id) is not None
                assert await LLMModel.objects.get(id=foreign_model.id) is not None
    finally:
        with tenant_scope(bypass=True):
            await Role.objects.set_permissions(role.id, original)
