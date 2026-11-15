"""P21-T13 increment B: real PostgreSQL acceptance of the dormant grant path.

Isolated task-owned schema, synthetic rows only, no public writes and no
provider traffic. The custody guards refuse to delete managed rows: the platform
fleet is retired, not dropped, and the owned schema drop removes it afterwards.
Every other synthetic row is removed here and the residue is verified.

The storage/guard matrix of increment A is not repeated; this file covers what
that evidence explicitly excluded: the grant resolver, the metadata listing, the
grant console service and grant freshness under a real transaction.
"""

from __future__ import annotations

import asyncio
from time import perf_counter
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, event, func, insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.database import async_engine
from app.core.permissions import set_current_user
from app.models.identity.role import PlatformRole
from app.models.identity.user import User
from app.models.llm.llm_catalog import LLMCatalog
from app.models.llm.llm_model import LLMModel
from app.models.llm.llm_provider import LLMProvider
from app.models.llm.plan_model_grant import PlanModelGrant
from app.models.tenancy.service_plan import ServicePlanPolicy, UserServicePlan
from app.models.tenancy.tenant import Tenant
from app.models.tenancy.tenant_user import TenantUser
from app.services.ai.catalog_grants import CatalogGrantResolver
from app.services.ai.dispatch_seam import (
    CODE_CATALOG_ACCESS_MISSING,
    CODE_PLAN_GRANT_MISSING,
    FUNDING_CLASS_PERSONAL,
    FUNDING_CLASS_PLATFORM,
    DispatchContext,
    DispatchDenial,
    DispatchFoundation,
    DispatchGrant,
    DispatchLease,
    DispatchRequest,
    DispatchReservation,
    admit,
)
from app.services.ai.grant_metadata_cache import MetadataRevisionCache
from app.services.llm_catalog import catalog_admin
from app.services.tenancy.service_plan_policy import resolve_holder_context

HOLDER = 9001
INVITEE = 9002
SUPERUSER = 9003
STARTER_INVITEE = 9004
TENANT = 9101
PLATFORM_UID = "p21t13-platform"
PERSONAL_UID = "p21t13-personal"


def _row(model_id, provider_id, capabilities):
    return SimpleNamespace(
        id=model_id,
        provider=SimpleNamespace(id=provider_id, api_format="openai"),
        capabilities=list(capabilities),
    )


def _request(model, capabilities):
    return DispatchRequest(
        model=model,
        purpose="chat",
        stage="chat",
        requires_capabilities=frozenset(capabilities),
    )


async def _grant_rows(session):
    rows = (await session.execute(select(PlanModelGrant))).scalars().all()
    return {(row.plan, row.capability, row.llm_model_id) for row in rows}


@pytest.fixture(scope="module")
async def world():
    """Synthetic holder, workspace, policies, platform fleet and grants."""
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        user_role = await session.scalar(select(PlatformRole).where(PlatformRole.codename == "user"))
        superuser_role = await session.scalar(select(PlatformRole).where(PlatformRole.codename == "superuser"))
        assert user_role is not None and superuser_role is not None, "the seeded platform roles are required"
        for user_id, username in (
            (HOLDER, "p21t13-holder"),
            (INVITEE, "p21t13-invitee"),
            (SUPERUSER, "p21t13-superuser"),
            (STARTER_INVITEE, "p21t13-starter-invitee"),
        ):
            session.add(
                User(
                    id=user_id,
                    username=username,
                    email=f"{username}@localhost.invalid",
                    hashed_password="synthetic-hash-never-a-credential",
                    is_active=True,
                    role_id=superuser_role.id if user_id == SUPERUSER else user_role.id,
                )
            )
        session.add(
            Tenant(
                id=TENANT,
                name="P21-T13 workspace",
                slug="p21t13-workspace",
                plan="pro",
                plan_holder_user_id=HOLDER,
                is_active=True,
            )
        )
        for user_id in (HOLDER, INVITEE, STARTER_INVITEE):
            session.add(
                TenantUser(
                    tenant_id=TENANT,
                    channel="web",
                    external_user_id=f"p21t13-{user_id}",
                    user_id=user_id,
                    is_active=True,
                )
            )
        limits = dict(Tenant.PLAN_LIMITS["pro"])
        limits["model_types"] = ["text", "image"]
        starter_limits = dict(Tenant.PLAN_LIMITS["starter"])
        starter_limits["model_types"] = ["text"]
        # Policy versions are immutable by design: an existing row is reused, and
        # the owned schema drop removes it afterwards.
        policy_versions = {}
        for plan_code, policy_limits in (("pro", limits), ("starter", starter_limits)):
            existing = await session.scalar(
                select(ServicePlanPolicy)
                .where(ServicePlanPolicy.plan_code == plan_code)
                .order_by(ServicePlanPolicy.policy_version.desc())
            )
            if existing is None:
                session.add(
                    ServicePlanPolicy(
                        plan_code=plan_code,
                        policy_version=1,
                        module_grants=["*"],
                        daily_quotas={},
                        resource_limits=policy_limits,
                        owned_workspace_limit_configured=True,
                        max_owned_workspaces=2,
                        token_policy_configured=False,
                    )
                )
                policy_versions[plan_code] = 1
            else:
                policy_versions[plan_code] = existing.policy_version
        session.add(
            UserServicePlan(user_id=HOLDER, plan_code="pro", policy_version=policy_versions["pro"], is_active=True)
        )
        session.add(
            UserServicePlan(
                user_id=INVITEE, plan_code="starter", policy_version=policy_versions["starter"], is_active=True
            )
        )
        session.add(
            UserServicePlan(
                user_id=STARTER_INVITEE,
                plan_code="starter",
                policy_version=policy_versions["starter"],
                is_active=True,
            )
        )
        await session.flush()

        platform = await session.scalar(select(LLMCatalog).where(LLMCatalog.kind == "platform"))
        if platform is None:
            session.add(LLMCatalog(id=9201, kind="platform", owner_user_id=None, is_active=True))
            await session.flush()
            platform = await session.scalar(select(LLMCatalog).where(LLMCatalog.kind == "platform"))
        else:
            platform.is_active = True
            await session.flush()
        assert platform is not None, "the platform catalog row must exist"
        catalog_id = platform.id

        provider = await session.scalar(select(LLMProvider).where(LLMProvider.uid == PLATFORM_UID))
        if provider is None:
            await session.execute(
                insert(LLMProvider.__table__).values(
                    id=9301,
                    catalog_id=catalog_id,
                    owner_user_id=None,
                    uid=PLATFORM_UID,
                    name="P21-T13 deployment provider",
                    api_format="openai",
                    base_url="https://provider.invalid",
                    is_active=True,
                    credential_version=1,
                )
            )
            provider_id = 9301
        else:
            provider_id = provider.id
            await session.execute(
                update(LLMProvider).where(LLMProvider.id == provider.id).values(catalog_id=catalog_id, is_active=True)
            )

        personal = await session.scalar(select(LLMProvider).where(LLMProvider.uid == PERSONAL_UID))
        if personal is None:
            await session.execute(
                insert(LLMProvider.__table__).values(
                    id=9302,
                    owner_user_id=HOLDER,
                    uid=PERSONAL_UID,
                    name="P21-T13 personal provider",
                    api_format="openai",
                    base_url="https://provider.invalid",
                    is_active=True,
                    credential_version=1,
                )
            )
            personal_id = 9302
        else:
            personal_id = personal.id
            await session.execute(update(LLMProvider).where(LLMProvider.id == personal.id).values(is_active=True))
        personal_catalog = await session.scalar(
            select(LLMCatalog).where(LLMCatalog.kind == "personal", LLMCatalog.owner_user_id == HOLDER)
        )
        assert personal_catalog is not None, "the custody trigger creates the holder's personal catalog"

        models = {}
        specs = (
            ("text", "p21t13-text", ["text"], "text", provider_id, None, catalog_id),
            ("text_image", "p21t13-text-image", ["text", "image"], "image,text", provider_id, None, catalog_id),
            ("text_video", "p21t13-text-video", ["text", "video"], "text,video", provider_id, None, catalog_id),
            ("starter", "p21t13-starter", ["text"], "text", provider_id, None, catalog_id),
            ("personal", "p21t13-personal-text", ["text"], "text", personal_id, HOLDER, personal_catalog.id),
        )
        for key, model_id_value, capabilities, csv, owner_provider_id, owner, owner_catalog in specs:
            existing = await session.scalar(select(LLMModel).where(LLMModel.model_id == model_id_value))
            if existing is None:
                await session.execute(
                    insert(LLMModel.__table__).values(
                        provider_id=owner_provider_id,
                        catalog_id=owner_catalog,
                        owner_user_id=owner,
                        name=model_id_value,
                        model_id=model_id_value,
                        model_type=csv,
                        capabilities=capabilities,
                        max_tokens=4096,
                        is_active=True,
                        is_default=False,
                    )
                )
                row = await session.scalar(select(LLMModel).where(LLMModel.model_id == model_id_value))
            else:
                row = existing
                await session.execute(
                    update(LLMModel)
                    .where(LLMModel.id == existing.id)
                    .values(
                        provider_id=owner_provider_id,
                        catalog_id=owner_catalog,
                        owner_user_id=owner,
                        model_type=csv,
                        capabilities=capabilities,
                        max_tokens=4096,
                        is_active=True,
                        is_default=False,
                    )
                )
            models[key] = row.id

        await session.execute(delete(PlanModelGrant).where(PlanModelGrant.llm_model_id.in_(list(models.values()))))
        session.add(
            PlanModelGrant(
                plan="pro",
                capability="text",
                llm_model_id=models["text"],
                is_active=True,
                priority=10,
                is_default=True,
            )
        )
        session.add(
            PlanModelGrant(
                plan="pro", capability="image", llm_model_id=models["text_image"], is_active=True, priority=20
            )
        )
        session.add(
            PlanModelGrant(
                plan="pro", capability="text", llm_model_id=models["text_video"], is_active=True, priority=30
            )
        )
        session.add(
            PlanModelGrant(
                plan="starter", capability="text", llm_model_id=models["starter"], is_active=True, priority=40
            )
        )
        await session.commit()
    yield {
        "maker": maker,
        "holder": HOLDER,
        "invitee": INVITEE,
        "starter_invitee": STARTER_INVITEE,
        "superuser": SUPERUSER,
        "tenant": TENANT,
        "catalog_id": catalog_id,
        "provider_id": provider_id,
        "personal_provider_id": personal_id,
        "personal_catalog_id": personal_catalog.id,
        "models": models,
    }
    async with maker() as session:
        await session.execute(delete(PlanModelGrant).where(PlanModelGrant.llm_model_id.in_(list(models.values()))))
        # The custody guard refuses to delete managed rows: the platform fleet is
        # retired here and the owned schema drop removes it afterwards.
        await session.execute(
            update(LLMModel)
            .where(LLMModel.id.in_((models["text"], models["text_image"], models["text_video"], models["starter"])))
            .values(is_active=False)
        )
        await session.execute(update(LLMProvider).where(LLMProvider.id == provider_id).values(is_active=False))
        await session.execute(update(LLMCatalog).where(LLMCatalog.id == catalog_id).values(is_active=False))
        await session.execute(delete(LLMModel).where(LLMModel.id == models["personal"]))
        await session.execute(delete(LLMProvider).where(LLMProvider.id == personal_id))
        await session.execute(delete(LLMCatalog).where(LLMCatalog.owner_user_id == HOLDER))
        await session.execute(
            delete(UserServicePlan).where(UserServicePlan.user_id.in_((HOLDER, INVITEE, STARTER_INVITEE)))
        )
        await session.execute(delete(TenantUser).where(TenantUser.tenant_id == TENANT))
        await session.execute(delete(Tenant).where(Tenant.id == TENANT))
        await session.execute(delete(User).where(User.id.in_((HOLDER, INVITEE, SUPERUSER, STARTER_INVITEE))))
        await session.commit()
        for table, column, values in (
            (PlanModelGrant, PlanModelGrant.llm_model_id, tuple(models.values())),
            (LLMModel, LLMModel.id, (models["personal"],)),
            (LLMProvider, LLMProvider.id, (personal_id,)),
            (UserServicePlan, UserServicePlan.user_id, (HOLDER, INVITEE, STARTER_INVITEE)),
            (TenantUser, TenantUser.tenant_id, (TENANT,)),
            (Tenant, Tenant.id, (TENANT,)),
            (User, User.id, (HOLDER, INVITEE, SUPERUSER, STARTER_INVITEE)),
        ):
            remaining = await session.scalar(select(func.count()).select_from(table).where(column.in_(values)))
            assert remaining == 0, f"{table.__name__} residue: {remaining}"
        personal_catalogs = await session.scalar(
            select(func.count()).select_from(LLMCatalog).where(LLMCatalog.owner_user_id == HOLDER)
        )
        assert personal_catalogs == 0, f"personal catalog residue: {personal_catalogs}"
        active = await session.scalar(
            select(func.count())
            .select_from(LLMModel)
            .where(
                LLMModel.id.in_((models["text"], models["text_image"], models["text_video"], models["starter"])),
                LLMModel.is_active.is_(True),
            )
        )
        assert active == 0, f"the platform fleet must be retired, not active: {active}"


async def _holder_context(session, world, *, actor, funding=FUNDING_CLASS_PLATFORM, delegation=None):
    fresh = await resolve_holder_context(session, world["tenant"], actor, lock_holder=False)
    return DispatchContext(
        actor_user_id=actor,
        tenant_id=world["tenant"],
        plan_holder_user_id=fresh.plan_holder_user_id,
        funding_class=funding,
        plan_policy_version=fresh.plan_policy_version,
        credential_owner_user_id=(None if funding == FUNDING_CLASS_PLATFORM else fresh.plan_holder_user_id),
        credential_version=7,
        credential_ref="deployment-alias",
        payer=f"holder:{fresh.plan_holder_user_id}",
        delegation_consent_version=delegation,
        workspace_plan=fresh,
    )


async def test_platform_grant_resolves_for_the_holder_and_an_invitee(world):
    async with world["maker"]() as session:
        for actor in (world["holder"], world["invitee"]):
            context = await _holder_context(session, world, actor=actor)
            resolver = CatalogGrantResolver(session)
            request = _request(
                _row(world["models"]["text"], world["provider_id"], ["text"]),
                ["text"],
            )
            grant = await resolver.resolve(request, context)
            assert isinstance(grant, DispatchGrant)
            assert grant.catalog_id == str(world["catalog_id"])


async def test_the_invitee_own_plan_never_grants_a_model(world):
    async with world["maker"]() as session:
        context = await _holder_context(session, world, actor=world["starter_invitee"])
        resolver = CatalogGrantResolver(session)
        metadata = await resolver.eligible_metadata(context, frozenset({"text"}))
        listed = {row.id for row in metadata}
        # The starter-only grant belongs to the invitee's own plan and must not
        # reach the holder's plan listing.
        assert world["models"]["starter"] not in listed
        assert world["models"]["text"] in listed


async def test_the_whole_capability_set_must_fit_the_holder_policy(world):
    async with world["maker"]() as session:
        context = await _holder_context(session, world, actor=world["holder"])
        resolver = CatalogGrantResolver(session)
        request = _request(
            _row(world["models"]["text_video"], world["provider_id"], ["text", "video"]),
            ["text"],
        )
        with pytest.raises(DispatchDenial) as denied:
            await resolver.resolve(request, context)
        assert denied.value.code == CODE_PLAN_GRANT_MISSING
        metadata = await resolver.eligible_metadata(context, frozenset({"text"}))
        assert world["models"]["text_video"] not in {row.id for row in metadata}


async def test_every_required_capability_needs_its_own_active_grant(world):
    async with world["maker"]() as session:
        context = await _holder_context(session, world, actor=world["holder"])
        resolver = CatalogGrantResolver(session)
        request = _request(
            _row(world["models"]["text_image"], world["provider_id"], ["text", "image"]),
            ["text", "image"],
        )
        with pytest.raises(DispatchDenial) as denied:
            await resolver.resolve(request, context)
        assert denied.value.code == CODE_PLAN_GRANT_MISSING
        image_only = await resolver.eligible_metadata(context, frozenset({"image"}))
        assert world["models"]["text_image"] in {row.id for row in image_only}


async def test_a_personal_catalog_row_needs_the_holder_or_delegated_consent(world):
    async with world["maker"]() as session:
        holder_context = await _holder_context(session, world, actor=world["holder"], funding=FUNDING_CLASS_PERSONAL)
        resolver = CatalogGrantResolver(session)
        request = _request(
            _row(world["models"]["personal"], world["personal_provider_id"], ["text"]),
            ["text"],
        )
        grant = await resolver.resolve(request, holder_context)
        assert grant.catalog_id == str(world["personal_catalog_id"])

        invitee_context = await _holder_context(session, world, actor=world["invitee"], funding=FUNDING_CLASS_PERSONAL)
        with pytest.raises(DispatchDenial) as denied:
            await resolver.resolve(request, invitee_context)
        assert denied.value.code == CODE_CATALOG_ACCESS_MISSING
        delegated = await _holder_context(
            session, world, actor=world["invitee"], funding=FUNDING_CLASS_PERSONAL, delegation=7
        )
        allowed = await resolver.resolve(request, delegated)
        assert allowed.catalog_id == str(world["personal_catalog_id"])


async def test_a_revoked_grant_is_refused_on_the_next_read(world):
    async with world["maker"]() as session:
        context = await _holder_context(session, world, actor=world["holder"])
        resolver = CatalogGrantResolver(session, cache=MetadataRevisionCache(max_entries=4))
        request = _request(_row(world["models"]["text"], world["provider_id"], ["text"]), ["text"])
        assert isinstance(await resolver.resolve(request, context), DispatchGrant)
        warmed = await resolver.eligible_metadata(context, frozenset({"text"}))
        assert world["models"]["text"] in {row.id for row in warmed}

        await session.execute(
            update(PlanModelGrant)
            .where(
                PlanModelGrant.plan == "pro",
                PlanModelGrant.capability == "text",
                PlanModelGrant.llm_model_id == world["models"]["text"],
            )
            .values(is_active=False)
        )
        await session.commit()
        with pytest.raises(DispatchDenial) as denied:
            await resolver.resolve(request, context)
        assert denied.value.code == CODE_PLAN_GRANT_MISSING
        after = await resolver.eligible_metadata(context, frozenset({"text"}))
        assert world["models"]["text"] not in {row.id for row in after}
        await session.execute(
            update(PlanModelGrant)
            .where(
                PlanModelGrant.plan == "pro",
                PlanModelGrant.capability == "text",
                PlanModelGrant.llm_model_id == world["models"]["text"],
            )
            .values(is_active=True)
        )
        await session.commit()
        assert isinstance(await resolver.resolve(request, context), DispatchGrant)


async def test_the_grant_console_service_writes_through_the_owner_gate(world):
    async with world["maker"]() as session:
        owner = await session.scalar(select(User).where(User.id == world["superuser"]))
        token = set_current_user(owner)
        try:
            row = await catalog_admin.save_grant(
                session,
                actor_user_id=world["superuser"],
                plan="pro",
                capability="video",
                model_id=world["models"]["text_video"],
                priority=25,
                is_active=True,
                is_default=True,
            )
            await session.commit()
            assert row.id is not None
            defaults = await session.scalar(
                select(func.count())
                .select_from(PlanModelGrant)
                .where(
                    PlanModelGrant.plan == "pro",
                    PlanModelGrant.capability == "video",
                    PlanModelGrant.is_active.is_(True),
                    PlanModelGrant.is_default.is_(True),
                )
            )
            assert defaults == 1
            retired = await catalog_admin.save_grant(
                session,
                actor_user_id=world["superuser"],
                plan="pro",
                capability="video",
                model_id=world["models"]["text_video"],
                priority=25,
                is_active=False,
                is_default=False,
                grant_id=row.id,
            )
            await session.commit()
            assert retired.is_active is False
            listed = await catalog_admin.ordered_grants(
                session, actor_user_id=world["superuser"], plan="pro", capability="video"
            )
            assert [grant.id for grant in listed] == [row.id]
        finally:
            from app.core.permissions import _current_user

            _current_user.reset(token)


async def test_a_non_owner_cannot_reach_the_grant_service(world):
    async with world["maker"]() as session:
        holder = await session.scalar(select(User).where(User.id == world["holder"]))
        token = set_current_user(holder)
        try:
            with pytest.raises(PermissionError):
                await catalog_admin.save_grant(
                    session,
                    actor_user_id=world["holder"],
                    plan="pro",
                    capability="text",
                    model_id=world["models"]["text"],
                )
        finally:
            from app.core.permissions import _current_user

            _current_user.reset(token)


async def test_concurrent_readers_keep_separate_sessions_and_results(world):
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(async_engine.sync_engine, "before_cursor_execute", capture)
    limit = asyncio.Semaphore(8)
    try:

        async def attempt():
            async with limit, world["maker"]() as session:
                context = await _holder_context(session, world, actor=world["holder"])
                resolver = CatalogGrantResolver(session)
                request = _request(_row(world["models"]["text"], world["provider_id"], ["text"]), ["text"])
                return await resolver.resolve(request, context)

        started = perf_counter()
        results = await asyncio.gather(*(attempt() for _ in range(200)))
        elapsed = perf_counter() - started
    finally:
        event.remove(async_engine.sync_engine, "before_cursor_execute", capture)
    assert all(isinstance(result, DispatchGrant) for result in results)
    assert {result.catalog_id for result in results} == {str(world["catalog_id"])}
    joined = " ".join(statements)
    assert "encrypted_api_key" not in joined
    assert "managed_credential_ref" not in joined
    assert "base_url" not in joined
    print(f"real-postgres concurrent attempts=200; bounded_concurrency=8; elapsed_seconds={elapsed:.6f}")


async def test_a_revoked_grant_stops_the_attempt_after_the_reservation(world):
    """The strict seam re-reads the grant: a revocation during reservation denies."""
    events = []
    async with world["maker"]() as session:
        context = await _holder_context(session, world, actor=world["holder"])
        request = _request(_row(world["models"]["text"], world["provider_id"], ["text"]), ["text"])
        resolver = CatalogGrantResolver(session)

        class Authority:
            async def resolve_identity(self, request, hints):
                return context

            async def resolve_credential(self, request, context):
                return context

            async def resolve_payer(self, request, context):
                return context

            async def revalidate(self, admission):
                return admission.context

        class Grants:
            async def resolve(self, request, context):
                return await resolver.resolve(request, context)

        class Rate:
            async def acquire(self, request, context):
                return DispatchLease(request.dispatch_id)

            async def release(self, admission):
                events.append("release")

        class Admission:
            async def check_allowance(self, request, context):
                return True

            async def reserve(self, request, context):
                # The grant is retired while the reservation is in flight.
                async with world["maker"]() as other:
                    await other.execute(
                        update(PlanModelGrant)
                        .where(
                            PlanModelGrant.plan == "pro",
                            PlanModelGrant.capability == "text",
                            PlanModelGrant.llm_model_id == world["models"]["text"],
                        )
                        .values(is_active=False)
                    )
                    await other.commit()
                events.append("reservation_committed")
                return DispatchReservation(request.dispatch_id, context.credential_version, {"receipt": "synthetic"})

        class Settlement:
            async def settle(self, admission, *, outcome, evidence=None):
                events.append(outcome)

        foundation = DispatchFoundation(
            authority=Authority(),
            grants=Grants(),
            rate=Rate(),
            admission=Admission(),
            settlement=Settlement(),
        )
        with pytest.raises(DispatchDenial) as denied:
            await admit(request, foundation=foundation)
        assert denied.value.code == CODE_PLAN_GRANT_MISSING
        assert events == ["reservation_committed", "not_dispatched", "release"]
    async with world["maker"]() as session:
        await session.execute(
            update(PlanModelGrant)
            .where(
                PlanModelGrant.plan == "pro",
                PlanModelGrant.capability == "text",
                PlanModelGrant.llm_model_id == world["models"]["text"],
            )
            .values(is_active=True)
        )
        await session.commit()
