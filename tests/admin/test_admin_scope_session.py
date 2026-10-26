"""The console's dedicated session enforces the same actor/workspace scope.

`BaseManager` is not the only thing SQLAdmin uses. Its own count/detail/form
queries, relationship loads and raw-FK writes bypass the manager layer entirely,
so the console runs them on `AdminAsyncSession`/`AdminSession`
([app/admin/session.py](../../app/admin/session.py)). These tests exercise that
session directly, against real rows in the suite's isolated schema:

- ORM lists, counts, details and relationship/FK queries see only the actor's
  personal rows and the one active workspace;
- a forged cross-workspace foreign key is refused at flush, not only hidden from
  the form;
- the platform owner keeps global workspace inspection but never gains another
  user's personal catalogue;
- no identity, or no workspace for workspace data, is a refusal — never a list.

Everything here is behavioral. Where a property could also be read off the
source, the assertion drives an actual query or flush instead.

Fixtures are written with the trusted setup scope (`tenant_scope(bypass=True)`
plus the explicit personal owner). Assertions run on the console session in the
real request scope. This module is `tenancy`-marked, so the autouse bypass is
off and "no scope" is the failure mode, not the default.
"""

import secrets
from contextlib import asynccontextmanager, contextmanager

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from app.admin.session import AdminAsyncSession
from app.core.database import async_engine
from app.core.permissions import permission_scope
from app.core.personal_catalog_context import catalog_execution_scope
from app.core.tenant_context import tenant_scope
from app.models import LLMModel, LLMProvider, PlatformRole, Source, Tenant, TenantUser, User
from app.types import PlatformRoleType

pytestmark = pytest.mark.tenancy

ADMIN_SESSION = async_sessionmaker(async_engine, class_=AdminAsyncSession, expire_on_commit=False)


@contextmanager
def setup_scope(owner: User | None = None):
    """Trusted arrange scope: platform data access, plus the named catalogue owner."""
    from app.core.permissions import operator_permission_scope

    with tenant_scope(bypass=True), operator_permission_scope():
        if owner is None:
            yield
        else:
            with catalog_execution_scope(owner.id):
                yield


def _uniq(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(5)}"


async def _user(prefix: str, role: PlatformRoleType = PlatformRoleType.ADMIN) -> User:
    with setup_scope():
        role_row = await PlatformRole.objects.get(codename=role.value)
        name = _uniq(prefix)
        created = await User.objects.create_user(
            username=name,
            email=f"{name}@example.com",
            password="scope-password-1",
            role_id=role_row.id,
        )
        return await User.objects.prefetch_related("role.permissions").get(id=created.id)


async def _workspace() -> Tenant:
    with setup_scope():
        slug = _uniq("session-ws")
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


async def _source(tenant: Tenant, label: str) -> Source:
    with setup_scope():
        return await Source.objects.create(
            tenant_id=tenant.id,
            name=_uniq(label),
            platform_id=1,
            source_type="GROUP",
            external_id=_uniq(label),
        )


async def _provider(owner: User, label: str) -> LLMProvider:
    with setup_scope(owner):
        return await LLMProvider.objects.create(
            uid=_uniq("sess-prov"),
            name=_uniq(label),
            api_format="openai",
            base_url="https://example.invalid/v1",
            is_active=True,
        )


async def _model(owner: User, provider: LLMProvider, label: str) -> LLMModel:
    with setup_scope(owner):
        return await LLMModel.objects.create(name=_uniq(label), model_id=_uniq(f"{label}-id"), provider_id=provider.id)


async def _member_grants(actor: User, tenant_id: int) -> frozenset:
    """The selected membership's grants, resolved exactly as `AdminScopeMiddleware` does."""
    from app.models.managers.tenant_manager import TenantUserManager

    manager = TenantUserManager()
    with setup_scope():
        memberships = await manager.web_memberships(actor.id)
        membership = next(row for row in memberships if row.tenant_id == tenant_id)
        return await manager.member_grants(membership)


@asynccontextmanager
async def _platform_grants(*resource_keys: str):
    """Give the platform `admin` role these resources' grants; restore in `finally`.

    The console session's read criterion (`_may_read_private`) asks for the
    actor's *platform* grant before it lets any private row through, and caps a
    workspace resource by the selected membership. Without this grant the
    platform `admin` role reads nothing and a "scope works" test would pass for
    the wrong reason.
    """
    from app.models import Permission, Role

    with setup_scope():
        role = await Role.objects.get(codename=PlatformRoleType.ADMIN.value)
        loaded = await Role.objects.get_with_permissions(role.id)
        original = [permission.id for permission in loaded.permissions]
        catalog = await Permission.objects.prefetch_related("model_type").all()
        selected = [p.id for p in catalog if p.model_type and p.model_type.resource_key in set(resource_keys)]
        await Role.objects.set_permissions(role.id, selected)
        from app.core.permissions import get_current_user

        bound = get_current_user()
        if bound is not None and bound.role_id == role.id:
            refreshed = await User.objects.prefetch_related("role.permissions").get(id=bound.id)
            bound.role = refreshed.role
    try:
        yield
    finally:
        with setup_scope():
            await Role.objects.set_permissions(role.id, original)
            restored = await Role.objects.get_with_permissions(role.id)
        assert sorted(p.id for p in restored.permissions) == sorted(original)


async def _console(actor: User, tenant_id: int | None = None):
    """The scope one console request enters: actor, one workspace, their catalogue.

    Mirrors `AdminScopeMiddleware`: `tenant_scope(selected)`,
    `catalog_execution_scope(actor.id)` and `permission_scope(actor,
    member_grants=<selected membership>)`, so a workspace write is authorized by
    that membership's role and never by the platform role.
    """
    grants = await _member_grants(actor, tenant_id) if tenant_id is not None else None
    return (
        tenant_scope(tenant_id),
        permission_scope(actor, member_grants=grants),
        catalog_execution_scope(actor.id),
    )


def _enter(*scopes):
    """Enter nested context managers in order; returns a list for a manual exit."""
    entered = []
    for scope in scopes:
        entered.append(scope)
        scope.__enter__()
    return entered


def _exit(entered) -> None:
    while entered:
        entered.pop().__exit__(None, None, None)


# --- workspace reads ------------------------------------------------------


@pytest.mark.asyncio
async def test_orm_lists_counts_and_details_are_limited_to_the_active_workspace():
    """SQLAdmin's list, its count badge and its details query read one workspace."""
    actor = await _user("sess-admin")
    active, foreign = await _workspace(), await _workspace()
    await _member(active, actor, "admin")
    mine = await _source(active, "active")
    theirs = await _source(foreign, "foreign")

    entered = _enter(*(await _console(actor, active.id)))
    try:
        async with _platform_grants("collection.source"):
            async with ADMIN_SESSION() as session:
                listed = list((await session.execute(select(Source))).scalars())
                assert [row.id for row in listed] == [mine.id]

                # The paginated list's own statement, count included.
                counted = (
                    await session.execute(select(func.count()).select_from(select(Source).subquery()))
                ).scalar_one()
                assert counted == 1

                detail = (await session.execute(select(Source).where(Source.id == theirs.id))).scalar_one_or_none()
                assert detail is None
                own_detail = (await session.execute(select(Source).where(Source.id == mine.id))).scalar_one_or_none()
                assert own_detail is not None
    finally:
        _exit(entered)


@pytest.mark.asyncio
async def test_the_scope_survives_a_relationship_query():
    """A relationship/`selectinload` read is filtered too, not only the root select."""
    actor = await _user("sess-rel-admin")
    active, foreign = await _workspace(), await _workspace()
    await _member(active, actor, "admin")
    await _source(active, "rel-active")
    await _source(foreign, "rel-foreign")

    entered = _enter(*(await _console(actor, active.id)))
    try:
        async with _platform_grants("tenancy.workspace", "collection.source"):
            async with ADMIN_SESSION() as session:
                workspace = (await session.execute(select(Tenant).where(Tenant.id == active.id))).scalar_one()
                assert workspace.id == active.id
                # A foreign workspace is not in the actor's scope even by primary key.
                assert (
                    await session.execute(select(Tenant).where(Tenant.id == foreign.id))
                ).scalar_one_or_none() is None
    finally:
        _exit(entered)

    # The relationship itself: a personal owner's models load, another's do not.
    owner, other = await _user("sess-rel-owner"), await _user("sess-rel-other")
    own_provider = await _provider(owner, "rel-own")
    foreign_provider = await _provider(other, "rel-foreign")
    own_model = await _model(owner, own_provider, "rel-own")
    await _model(other, foreign_provider, "rel-foreign")

    entered = _enter(*(await _console(owner)))
    try:
        async with _platform_grants("llm.provider", "llm.model"):
            async with ADMIN_SESSION() as session:
                loaded = (
                    await session.execute(
                        select(LLMProvider)
                        .where(LLMProvider.id == own_provider.id)
                        .options(selectinload(LLMProvider.models))
                    )
                ).scalar_one()
                assert [row.id for row in loaded.models] == [own_model.id]
                # And a foreign provider cannot be reached by id at all.
                assert (
                    await session.execute(select(LLMProvider).where(LLMProvider.id == foreign_provider.id))
                ).scalar_one_or_none() is None
    finally:
        _exit(entered)


@pytest.mark.asyncio
async def test_a_workspace_read_without_a_selected_workspace_is_a_refusal():
    """No active workspace means no workspace rows — not every workspace's rows."""
    actor = await _user("sess-nows")
    workspace = await _workspace()
    await _member(workspace, actor, "admin")
    await _source(workspace, "ws-row")

    entered = _enter(*(await _console(actor, None)))
    try:
        # The grant is present: the empty result must come from the missing
        # workspace selection, not from the actor lacking the right.
        async with _platform_grants("collection.source"):
            async with ADMIN_SESSION() as session:
                assert list((await session.execute(select(Source))).scalars()) == []
    finally:
        _exit(entered)


@pytest.mark.asyncio
async def test_an_anonymous_console_session_reads_nothing_private():
    """With no actor bound, private rows are unreachable rather than global."""
    workspace = await _workspace()
    await _source(workspace, "anon-row")
    owner = await _user("sess-anon-owner")
    await _provider(owner, "anon-provider")

    async with ADMIN_SESSION() as session:
        assert list((await session.execute(select(Source))).scalars()) == []
        assert list((await session.execute(select(LLMProvider))).scalars()) == []


# --- supervisor bypass boundary -------------------------------------------


@pytest.mark.asyncio
async def test_the_platform_owner_reads_every_workspace_but_only_their_catalogue():
    """Global workspace inspection is the owner's; the personal catalogue is not."""
    owner = await _user("sess-superuser", PlatformRoleType.SUPERUSER)
    first_ws, second_ws = await _workspace(), await _workspace()
    first = await _source(first_ws, "owner-ws-1")
    second = await _source(second_ws, "owner-ws-2")
    other_user = await _user("sess-superuser-other")
    own_provider = await _provider(owner, "owner-provider")
    foreign_provider = await _provider(other_user, "foreign-provider")

    entered = _enter(tenant_scope(bypass=True), permission_scope(owner), catalog_execution_scope(owner.id))
    try:
        async with ADMIN_SESSION() as session:
            workspace_ids = {row.id for row in (await session.execute(select(Source))).scalars()}
            assert workspace_ids == {first.id, second.id}
            provider_ids = {row.id for row in (await session.execute(select(LLMProvider))).scalars()}
            # Same session, same bypass: the personal catalogue is still
            # `owner_user_id`-scoped, so the owner does not see another user's.
            assert provider_ids == {own_provider.id}
            assert foreign_provider.id not in provider_ids
    finally:
        _exit(entered)


# --- workspace writes -----------------------------------------------------


@pytest.mark.asyncio
async def test_a_forged_cross_workspace_foreign_key_is_refused_at_flush():
    """A raw FK from another workspace is refused by the session, not just hidden.

    This is the forged-POST case: the form no longer offers the row, but SQLAdmin
    writes many-to-one relationships as raw FK values. The session must validate
    them in the same transaction.
    """
    actor = await _user("sess-fk")
    active, foreign = await _workspace(), await _workspace()
    await _member(active, actor, "admin")
    own_source = await _source(active, "fk-own")
    own_scenario = await _scenario(active, "fk-own")
    foreign_scenario = await _scenario(foreign, "fk-foreign")

    entered = _enter(*(await _console(actor, active.id)))
    try:
        async with _platform_grants("scheduling.task", "collection.source", "analysis.scenario"):
            async with ADMIN_SESSION() as session:
                # A legitimate link inside the active workspace goes through.
                task = _task(active, own_scenario.id, "fk-allowed")
                task.sources = [own_source]
                session.add(task)
                await session.flush()
                assert task.id is not None

            async with ADMIN_SESSION() as session:
                forged = _task(active, foreign_scenario.id, "fk-forged")
                session.add(forged)
                with pytest.raises(HTTPException) as refused:
                    await session.flush()
                assert refused.value.status_code == 403
                await session.rollback()
    finally:
        _exit(entered)

    with setup_scope():
        from app.models import AgentTask

        # The refused write left no row anywhere.
        assert await AgentTask.objects.get(name=forged.name) is None


@pytest.mark.asyncio
async def test_a_workspace_write_without_an_actor_is_refused():
    """The session is fail-closed: no actor means no write at all."""
    actor = await _user("sess-write-anon")
    workspace = await _workspace()
    await _member(workspace, actor, "admin")

    async with ADMIN_SESSION() as session:
        task = _task(workspace, None, "anon-write")
        session.add(task)
        with pytest.raises(HTTPException) as refused:
            await session.flush()
        assert refused.value.status_code == 403
        await session.rollback()

    with setup_scope():
        from app.models import AgentTask

        assert await AgentTask.objects.get(name=task.name) is None


# --- helpers --------------------------------------------------------------


def _task(tenant: Tenant, scenario_id: int | None, label: str):
    from app.models import AgentTask

    return AgentTask(
        tenant_id=tenant.id,
        name=_uniq(label),
        cron_expr="0 9 * * *",
        job_type="collect",
        agent_scenario_id=scenario_id,
    )


async def _scenario(tenant: Tenant, label: str):
    from app.models import AgentScenario

    with setup_scope():
        return await AgentScenario.objects.create(name=_uniq(label), tenant_id=tenant.id)
