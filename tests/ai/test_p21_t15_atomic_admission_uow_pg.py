"""P21-T15 real PostgreSQL UOW rollback across USD and token reservations."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select, text

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.tenant_context import tenant_scope
from app.models import (
    LLMDispatchLease,
    LLMTokenAllowanceCycle,
    LLMTokenAllowanceReservation,
    PlatformRole,
    Tenant,
    User,
)
from app.models.llm.llm_budget import LLMBudgetDay, LLMBudgetReservation
from app.models.llm.llm_dispatch_receipt import LLMDispatchReceipt
from app.services.ai.atomic_admission import AtomicLLMAdmissionService, TokenAllowanceExhausted, reserve_token_ceiling
from app.services.ai.dispatch_seam import FUNDING_CLASS_PLATFORM, DispatchContext, DispatchDenial, DispatchRequest


@pytest.mark.asyncio
async def test_token_denial_rolls_back_real_tenant_usd_receipt_and_day_reservation(monkeypatch):
    target_schema = os.environ.get("P21_T15_ISOLATED_SCHEMA")
    if not target_schema:
        pytest.skip("P21_T15_ISOLATED_SCHEMA must name the task-owned isolated schema")
    if settings.DB_SCHEMA != target_schema or target_schema in {"public", "test_schema"}:
        pytest.fail("P21-T15 UOW rollback requires its own non-public, non-shared schema")

    suffix = uuid.uuid4().hex[:12]
    username = f"p21t15-uow-{suffix}"
    tenant_id = user_id = None
    dispatch_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        tables = await session.execute(
            text("SELECT to_regclass(:cycles), to_regclass(:reservations), to_regclass(:receipts)"),
            {
                "cycles": f"{target_schema}.llm_token_allowance_cycles",
                "reservations": f"{target_schema}.llm_token_allowance_reservations",
                "receipts": f"{target_schema}.llm_dispatch_receipts",
            },
        )
        assert all(tables.one()), "Apply P21-T15 and prerequisite migrations before this test"
        role = await session.scalar(select(PlatformRole).where(PlatformRole.codename == "user"))
        assert role is not None
        holder = User(
            username=username,
            email=f"{username}@localhost.invalid",
            hashed_password="synthetic-hash-never-a-credential",
            role_id=role.id,
            is_active=True,
        )
        session.add(holder)
        await session.flush()
        user_id = holder.id
        tenant = Tenant(
            name=f"P21-T15 UOW {suffix}",
            slug=f"p21t15-uow-{suffix}",
            plan="pro",
            plan_holder_user_id=holder.id,
            is_active=True,
        )
        session.add(tenant)
        await session.flush()
        tenant_id = tenant.id
        day = now.date()
        session.add_all(
            [
                LLMBudgetDay(
                    tenant_id=tenant.id,
                    utc_day=day,
                    settled_usd=Decimal(0),
                    reserved_usd=Decimal(0),
                    undefined_count=0,
                    unbounded_count=0,
                    frozen=False,
                    baseline_version="p21t15-uow-test-v1",
                ),
                LLMTokenAllowanceCycle(
                    plan_holder_user_id=holder.id,
                    allowance_pool="provider_reported_billable_tokens",
                    cycle_started_at=now - timedelta(minutes=1),
                    cycle_ends_at=now + timedelta(days=30),
                    policy_version=5,
                    token_limit=10,
                    reserved_tokens=10,
                    consumed_tokens=0,
                ),
            ]
        )
        await session.commit()

    async def resolve_policy(*_, **__):
        return SimpleNamespace(
            plan_holder_user_id=user_id,
            plan_policy_version=5,
            actor_user_id=user_id,
            policy=SimpleNamespace(token_policy=SimpleNamespace(unit="token", token_limit=10, policy_version=5)),
        )

    monkeypatch.setattr("app.services.ai.atomic_admission.resolve_holder_context", resolve_policy)
    monkeypatch.setattr("app.services.tenancy.resolver.resolved_daily_cost_limit", lambda _: 10.0)

    model = SimpleNamespace(
        id=7101,
        model_id="p21t15-uow-model",
        context_window_tokens=32,
        max_output_tokens=8,
        input_cost_per_1k=Decimal("0.1"),
        output_cost_per_1k=Decimal("0.2"),
        pricing_token_unit=1000,
        cache_read_cost_per_1m=Decimal(0),
        cache_write_5m_cost_per_1m=Decimal(0),
        cache_write_1h_cost_per_1m=Decimal(0),
        provider=SimpleNamespace(id=7201, name="p21t15-uow-provider"),
    )
    request = DispatchRequest(model=model, purpose="chat", stage="chat", dispatch_id=dispatch_id)
    context = DispatchContext(
        actor_user_id=user_id,
        tenant_id=tenant_id,
        plan_holder_user_id=user_id,
        plan_policy_version=5,
        catalog_id="p21t15-test-catalog",
        funding_class="platform",
        payer=f"tenant:{tenant_id}",
        credential_version=1,
    )
    try:
        with tenant_scope(tenant_id):
            with pytest.raises(DispatchDenial, match="token_allowance_exhausted"):
                await AtomicLLMAdmissionService().reserve(request, context)

        # Verify committed fixture is unchanged and both attempted USD writes
        # vanished with the token-cap denial in the same PostgreSQL transaction.
        async with async_session_maker() as session:
            receipt = await session.scalar(
                select(LLMDispatchReceipt).where(LLMDispatchReceipt.dispatch_id == dispatch_id)
            )
            reservation = await session.scalar(
                select(LLMBudgetReservation).where(LLMBudgetReservation.dispatch_id == dispatch_id)
            )
            balance = await session.scalar(
                select(LLMBudgetDay).where(LLMBudgetDay.tenant_id == tenant_id, LLMBudgetDay.utc_day == day)
            )
            cycle = await session.scalar(
                select(LLMTokenAllowanceCycle).where(LLMTokenAllowanceCycle.plan_holder_user_id == user_id)
            )
            assert receipt is None
            assert reservation is None
            assert balance.reserved_usd == Decimal(0)
            assert cycle.reserved_tokens == 10
            assert cycle.consumed_tokens == 0
    finally:
        async with async_session_maker() as session:
            await session.execute(
                delete(LLMTokenAllowanceCycle).where(LLMTokenAllowanceCycle.plan_holder_user_id == user_id)
            )
            await session.execute(delete(LLMBudgetDay).where(LLMBudgetDay.tenant_id == tenant_id))
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()
            assert (
                await session.scalar(
                    select(text("count(*)"))
                    .select_from(LLMTokenAllowanceCycle)
                    .where(LLMTokenAllowanceCycle.plan_holder_user_id == user_id)
                )
                == 0
            )


@pytest.mark.asyncio
async def test_postgres_zero_unlimited_and_independent_holder_pools():
    target_schema = os.environ.get("P21_T15_ISOLATED_SCHEMA")
    if not target_schema:
        pytest.skip("P21_T15_ISOLATED_SCHEMA must name the task-owned isolated schema")
    if settings.DB_SCHEMA != target_schema or target_schema in {"public", "test_schema"}:
        pytest.fail("P21-T15 allowance test requires its own non-public, non-shared schema")

    suffix = uuid.uuid4().hex[:12]
    names = [f"p21t15-pool-{suffix}-{index}" for index in range(3)]
    user_ids: list[int] = []
    async with async_session_maker() as session:
        role = await session.scalar(select(PlatformRole).where(PlatformRole.codename == "user"))
        assert role is not None
        users = [
            User(
                username=name,
                email=f"{name}@localhost.invalid",
                hashed_password="synthetic-hash-never-a-credential",
                role_id=role.id,
                is_active=True,
            )
            for name in names
        ]
        session.add_all(users)
        await session.flush()
        user_ids = [user.id for user in users]
        await session.commit()

    now = datetime.now(timezone.utc)
    try:
        # Explicit zero denies positive use, and NULL is unlimited even at a
        # large amount. Finite pools remain independent by holder in PostgreSQL.
        with pytest.raises(TokenAllowanceExhausted):
            async with async_session_maker() as session:
                async with session.begin():
                    await reserve_token_ceiling(
                        session,
                        holder_user_id=user_ids[0],
                        policy_version=1,
                        token_limit=0,
                        reserved_tokens=1,
                        now=now,
                    )
        async with async_session_maker() as session:
            async with session.begin():
                unlimited = await reserve_token_ceiling(
                    session,
                    holder_user_id=user_ids[0],
                    policy_version=1,
                    token_limit=None,
                    reserved_tokens=1_000_000,
                    now=now,
                )
                assert unlimited.reserved_tokens == 1_000_000
        async with async_session_maker() as session:
            async with session.begin():
                await reserve_token_ceiling(
                    session,
                    holder_user_id=user_ids[1],
                    policy_version=1,
                    token_limit=10,
                    reserved_tokens=10,
                    now=now,
                )
                await reserve_token_ceiling(
                    session,
                    holder_user_id=user_ids[2],
                    policy_version=1,
                    token_limit=10,
                    reserved_tokens=10,
                    now=now,
                )
        async with async_session_maker() as session:
            cycles = (
                (
                    await session.execute(
                        select(LLMTokenAllowanceCycle).where(LLMTokenAllowanceCycle.plan_holder_user_id.in_(user_ids))
                    )
                )
                .scalars()
                .all()
            )
            assert {(row.plan_holder_user_id, row.reserved_tokens) for row in cycles} == {
                (user_ids[0], 1_000_000),
                (user_ids[1], 10),
                (user_ids[2], 10),
            }
    finally:
        async with async_session_maker() as session:
            await session.execute(
                delete(LLMTokenAllowanceCycle).where(LLMTokenAllowanceCycle.plan_holder_user_id.in_(user_ids))
            )
            await session.execute(delete(User).where(User.id.in_(user_ids)))
            await session.commit()
            remaining = await session.scalar(
                select(text("count(*)"))
                .select_from(LLMTokenAllowanceCycle)
                .where(LLMTokenAllowanceCycle.plan_holder_user_id.in_(user_ids))
            )
            assert remaining == 0


@pytest.mark.asyncio
async def test_real_admission_serializes_two_tenants_on_one_holder_without_test_lock(monkeypatch):
    """Race actual UOWs; only the service's own ordered locks may serialize them."""
    target_schema = os.environ.get("P21_T15_ISOLATED_SCHEMA")
    if not target_schema:
        pytest.skip("P21_T15_ISOLATED_SCHEMA must name the task-owned isolated schema")
    if settings.DB_SCHEMA != target_schema or target_schema in {"public", "test_schema"}:
        pytest.fail("P21-T15 service race requires its own non-public, non-shared schema")

    suffix = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc)
    dispatch_ids = [uuid.uuid4().hex, uuid.uuid4().hex]
    holder_id = None
    tenant_ids: list[int] = []
    async with async_session_maker() as session:
        role = await session.scalar(select(PlatformRole).where(PlatformRole.codename == "user"))
        assert role is not None
        holder = User(
            username=f"p21t15-race-owner-{suffix}",
            email=f"p21t15-race-owner-{suffix}@localhost.invalid",
            hashed_password="synthetic-hash-never-a-credential",
            role_id=role.id,
            is_active=True,
        )
        invitee = User(
            username=f"p21t15-race-invitee-{suffix}",
            email=f"p21t15-race-invitee-{suffix}@localhost.invalid",
            hashed_password="synthetic-hash-never-a-credential",
            role_id=role.id,
            is_active=True,
        )
        session.add_all((holder, invitee))
        await session.flush()
        holder_id = holder.id
        tenants = [
            Tenant(
                name=f"P21-T15 race {suffix}-{index}",
                slug=f"p21t15-race-{suffix}-{index}",
                plan="pro",
                plan_holder_user_id=holder.id,
                is_active=True,
            )
            for index in range(2)
        ]
        session.add_all(tenants)
        await session.flush()
        tenant_ids = [tenant.id for tenant in tenants]
        session.add_all(
            [
                LLMBudgetDay(
                    tenant_id=tenant.id,
                    utc_day=now.date(),
                    settled_usd=Decimal(0),
                    reserved_usd=Decimal(0),
                    undefined_count=0,
                    unbounded_count=0,
                    frozen=False,
                    baseline_version="p21t15-service-race-v1",
                )
                for tenant in tenants
            ]
        )
        session.add_all(
            [
                LLMDispatchLease(
                    lease_id=dispatch_id,
                    scope="upstream",
                    scope_key="p21t15-test-provider",
                    model_family="p21t15-test-family",
                    expires_at=now + timedelta(minutes=5),
                )
                for dispatch_id in dispatch_ids
            ]
        )
        await session.commit()

    from app.services.ai import atomic_admission as admission_module

    async def resolve_policy(_session, tenant_id, actor_user_id, **_):
        assert tenant_id in tenant_ids
        return SimpleNamespace(
            plan_holder_user_id=holder_id,
            plan_policy_version=7,
            actor_user_id=actor_user_id,
            policy=SimpleNamespace(token_policy=SimpleNamespace(unit="token", token_limit=10, policy_version=7)),
        )

    monkeypatch.setattr(admission_module, "resolve_holder_context", resolve_policy)
    monkeypatch.setattr("app.services.tenancy.resolver.resolved_daily_cost_limit", lambda _: 10.0)

    model = SimpleNamespace(
        id=7301,
        model_id="p21t15-race-model",
        context_window_tokens=7,
        max_output_tokens=1,
        pricing_token_unit=1000,
        input_cost_per_1k=Decimal("0.1"),
        output_cost_per_1k=Decimal("0.2"),
        cache_read_cost_per_1m=Decimal(0),
        cache_write_5m_cost_per_1m=Decimal(0),
        cache_write_1h_cost_per_1m=Decimal(0),
        provider=SimpleNamespace(id=7302, name="p21t15-race-provider"),
    )
    actors = [holder_id, invitee.id]

    async def reserve(index):
        context = DispatchContext(
            actor_user_id=actors[index],
            tenant_id=tenant_ids[index],
            plan_holder_user_id=holder_id,
            plan_policy_version=7,
            catalog_id="p21t15-race-test-catalog",
            funding_class=FUNDING_CLASS_PLATFORM,
            payer=f"tenant:{tenant_ids[index]}",
            credential_version=1,
            delegation_consent_version=1 if actors[index] != holder_id else None,
        )
        request = DispatchRequest(model=model, purpose="chat", stage="chat", dispatch_id=dispatch_ids[index])
        with tenant_scope(tenant_ids[index]):
            return await AtomicLLMAdmissionService().reserve(request, context)

    outcomes = await asyncio.gather(reserve(0), reserve(1), return_exceptions=True)
    assert sum(not isinstance(value, BaseException) for value in outcomes) == 1
    denied = [value for value in outcomes if isinstance(value, DispatchDenial)]
    assert len(denied) == 1
    assert denied[0].code == "token_allowance_exhausted"

    async with async_session_maker() as session:
        cycle = await session.scalar(
            select(LLMTokenAllowanceCycle).where(LLMTokenAllowanceCycle.plan_holder_user_id == holder_id)
        )
        assert cycle is not None
        assert cycle.reserved_tokens == 7
        token_rows = (
            (
                await session.execute(
                    select(LLMTokenAllowanceReservation).where(
                        LLMTokenAllowanceReservation.dispatch_id.in_(dispatch_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(token_rows) == 1
        assert token_rows[0].reserved_tokens == 7
        # The successful workspace owns one USD liability; the losing attempt's
        # receipt and reservation were rolled back with its token denial.
        usd_rows = (
            (
                await session.execute(
                    select(LLMBudgetReservation).where(
                        LLMBudgetReservation.tenant_id.in_(tenant_ids),
                        LLMBudgetReservation.dispatch_id.in_(dispatch_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(usd_rows) == 1
        assert usd_rows[0].dispatch_id == token_rows[0].dispatch_id
