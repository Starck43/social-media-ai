"""P21-T15 two-session holder-pool race; requires an isolated task-owned schema."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select, text

from app.core.config import settings
from app.core.database import async_session_maker
from app.models import LLMTokenAllowanceCycle, PlatformRole, Tenant, TenantUser, User
from app.services.ai.atomic_admission import TokenAllowanceExhausted, reserve_token_ceiling

__source_only__ = True


@pytest.mark.asyncio
async def test_two_sessions_share_one_holder_pool_across_workspaces_and_invitees():
    target_schema = os.environ.get("P21_T15_ISOLATED_SCHEMA")
    if not target_schema:
        pytest.skip("P21_T15_ISOLATED_SCHEMA must name the task-owned isolated schema")
    if settings.DB_SCHEMA != target_schema or target_schema in {"public", "test_schema"}:
        pytest.fail("P21-T15 PostgreSQL race requires its own non-public, non-shared schema")

    holder_name = f"p21t15-holder-{uuid.uuid4().hex[:12]}"
    invitee_name = f"p21t15-invitee-{uuid.uuid4().hex[:12]}"
    suffix = uuid.uuid4().hex[:12]
    tenant_ids: list[int] = []
    user_ids: list[int] = []
    async with async_session_maker() as session:
        token_tables = (
            await session.execute(
                text("SELECT to_regclass(:cycles), to_regclass(:reservations)"),
                {
                    "cycles": f"{settings.DB_SCHEMA}.llm_token_allowance_cycles",
                    "reservations": f"{settings.DB_SCHEMA}.llm_token_allowance_reservations",
                },
            )
        ).one()
        assert all(token_tables), "Apply the P21-T15 migration to the task-owned schema first"
        user_role = await session.scalar(select(PlatformRole).where(PlatformRole.codename == "user"))
        assert user_role is not None, "Seeded user role is required in the isolated schema"
        holder = User(
            username=holder_name,
            email=f"{holder_name}@localhost.invalid",
            hashed_password="synthetic-hash-never-a-credential",
            is_active=True,
            role_id=user_role.id,
        )
        invitee = User(
            username=invitee_name,
            email=f"{invitee_name}@localhost.invalid",
            hashed_password="synthetic-hash-never-a-credential",
            is_active=True,
            role_id=user_role.id,
        )
        session.add_all((holder, invitee))
        await session.flush()
        user_ids = [holder.id, invitee.id]
        tenants = [
            Tenant(
                name=f"P21-T15 {label}",
                slug=f"p21t15-{label}-{suffix}",
                plan="pro",
                plan_holder_user_id=holder.id,
                is_active=True,
            )
            for label in ("owner-space", "invitee-space")
        ]
        session.add_all(tenants)
        await session.flush()
        tenant_ids = [tenant.id for tenant in tenants]
        session.add_all(
            [
                TenantUser(
                    tenant_id=tenants[0].id,
                    channel="web",
                    external_user_id=f"p21t15-{suffix}-holder",
                    user_id=holder.id,
                    is_active=True,
                ),
                TenantUser(
                    tenant_id=tenants[1].id,
                    channel="web",
                    external_user_id=f"p21t15-{suffix}-invitee",
                    user_id=invitee.id,
                    is_active=True,
                ),
            ]
        )
        await session.commit()

    now = datetime.now(timezone.utc)

    async def reserve(actor_id: int, tenant_id: int):
        async with async_session_maker() as session:
            async with session.begin():
                # Each contender carries different workspace/actor context, but
                # both serialize on the same holder row and use no tenant key.
                locked = await session.scalar(select(User.id).where(User.id == user_ids[0]).with_for_update())
                assert locked == user_ids[0]
                assert actor_id > 0 and tenant_id in tenant_ids
                return await reserve_token_ceiling(
                    session,
                    holder_user_id=user_ids[0],
                    policy_version=7,
                    token_limit=10,
                    reserved_tokens=7,
                    now=now,
                )

    try:
        results = await asyncio.gather(
            reserve(user_ids[0], tenant_ids[0]),
            reserve(user_ids[1], tenant_ids[1]),
            return_exceptions=True,
        )
        assert sum(not isinstance(result, Exception) for result in results) == 1
        assert sum(isinstance(result, TokenAllowanceExhausted) for result in results) == 1
        async with async_session_maker() as session:
            cycles = (
                (
                    await session.execute(
                        select(LLMTokenAllowanceCycle).where(LLMTokenAllowanceCycle.plan_holder_user_id == user_ids[0])
                    )
                )
                .scalars()
                .all()
            )
            assert len(cycles) == 1
            assert cycles[0].reserved_tokens == 7
            assert cycles[0].consumed_tokens == 0
    finally:
        async with async_session_maker() as session:
            await session.execute(delete(TenantUser).where(TenantUser.tenant_id.in_(tenant_ids)))
            await session.execute(
                delete(LLMTokenAllowanceCycle).where(LLMTokenAllowanceCycle.plan_holder_user_id == user_ids[0])
            )
            await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
            await session.execute(delete(User).where(User.id.in_(user_ids)))
            await session.commit()
            residue = await session.scalar(
                select(text("count(*)"))
                .select_from(LLMTokenAllowanceCycle)
                .where(LLMTokenAllowanceCycle.plan_holder_user_id == user_ids[0])
            )
            assert residue == 0
