"""What a console actor is allowed to see, once the scope is bound.

`AdminScopeMiddleware` decides the ambient scope; these tests pin what the
managers and the permission checks then do with it, against real rows:

- the owner's console keeps the platform bypass — every user, every workspace;
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
    with catalog_execution_scope(owner.id):
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


def _scope(rows: list[tuple[int, frozenset]]):
    """The read scope a member runs in: active workspace first, all readable."""
    active, _ = rows[0]
    return tenant_scope(active, also=tuple(tenant_id for tenant_id, _ in rows))


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
async def test_the_owner_reads_every_catalog_through_the_bypass():
    """The owner is the one actor that keeps `is_bypass()` on `/admin`."""
    first, second = await _user("scope-c"), await _user("scope-d")
    mine = await _provider(first, "owner-visible-1")
    theirs = await _provider(second, "owner-visible-2")

    with tenant_scope(bypass=True):
        ids = {row.id for row in await LLMProvider.objects.all()}
    assert {mine.id, theirs.id} <= ids


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
    with catalog_execution_scope(actor.id):
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
    with _scope(rows), permission_scope(helper, member_grants=rows[0][1]):
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

