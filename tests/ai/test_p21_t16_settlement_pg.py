"""P21-T16 settlement integration checks on an owned PostgreSQL schema."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.tenant_context import tenant_scope
from app.models import (
    LLMBudgetDay,
    LLMBudgetReservation,
    LLMDispatchReceipt,
    LLMDispatchReservationEvent,
    LLMDispatchReservationState,
    LLMOutcomeEvent,
    LLMTokenAllowanceCycle,
    LLMTokenAllowanceReservation,
    PlatformRole,
    Tenant,
    User,
)
from app.services.ai.dispatch_seam import DispatchAdmission, DispatchContext, DispatchRequest
from app.services.ai.settlement import LLMSettlementService


def isolated_schema() -> str:
    schema = os.environ.get("P21_T16_ISOLATED_SCHEMA")
    if not schema:
        pytest.skip("P21_T16_ISOLATED_SCHEMA must name the task-owned isolated schema")
    if settings.DB_SCHEMA != schema or schema in {"public", "test_schema"}:
        pytest.fail("P21-T16 PostgreSQL acceptance requires its own non-public, non-shared schema")
    return schema


async def create_attempt(*, lease_expired: bool = False, prepared_at: datetime | None = None):
    schema = isolated_schema()
    dispatch_id = uuid.uuid4().hex
    suffix = dispatch_id[:12]
    now = prepared_at or datetime.now(timezone.utc)
    async with async_session_maker() as session:
        required_tables = await session.execute(
            text("SELECT to_regclass(:receipt), to_regclass(:state), to_regclass(:events)"),
            {
                "receipt": f"{schema}.llm_dispatch_receipts",
                "state": f"{schema}.llm_dispatch_reservation_states",
                "events": f"{schema}.llm_dispatch_reservation_events",
            },
        )
        assert all(required_tables.one()), "Apply P21-T15 migrations to the task-owned schema first"
        role = await session.scalar(select(PlatformRole).where(PlatformRole.codename == "user"))
        assert role is not None, "Seeded user role is required"
        user = User(
            username=f"p21t16-{suffix}",
            email=f"p21t16-{suffix}@localhost.invalid",
            hashed_password="synthetic-hash-never-a-credential",
            role_id=role.id,
            is_active=True,
        )
        session.add(user)
        await session.flush()
        tenant = Tenant(
            name=f"P21-T16 {suffix}",
            slug=f"p21t16-{suffix}",
            plan="pro",
            plan_holder_user_id=user.id,
            is_active=True,
        )
        session.add(tenant)
        await session.flush()
        day = now.date()
        session.add(
            LLMDispatchReceipt(
                tenant_id=tenant.id,
                dispatch_id=dispatch_id,
                purpose="chat",
                stage="chat",
                prepared_at=now,
                utc_day=day,
                llm_model_row_id=9101,
                llm_provider_row_id=9102,
                model_id="p21t16-model",
                provider_name="p21t16-provider",
                input_cost_per_1k=Decimal("0.1"),
                output_cost_per_1k=Decimal("0.2"),
                cache_read_cost_per_1m=None,
                cache_write_5m_cost_per_1m=None,
                cache_write_1h_cost_per_1m=None,
                pricing_token_unit=1000,
                tariff_state="known",
                pricing_policy_version="provider-price-v1",
                source_id=None,
                job_id=None,
                agent_task_id=None,
                agent_session_id=None,
            )
        )
        # Reservation guard triggers query the immutable receipt immediately.
        await session.flush()
        session.add(
            LLMBudgetDay(
                tenant_id=tenant.id,
                utc_day=day,
                settled_usd=Decimal(0),
                reserved_usd=Decimal("1"),
                undefined_count=0,
                unbounded_count=0,
                frozen=False,
                baseline_version="p21t16-test-v1",
            )
        )
        session.add(
            LLMBudgetReservation(
                tenant_id=tenant.id,
                utc_day=day,
                dispatch_id=dispatch_id,
                ceiling_state="bounded",
                ceiling_usd=Decimal("1"),
                bound_version="p21t16-bound-v1",
                undefined_policy_version="owner-undefined-allow-v1",
                cap_usd=Decimal("10"),
            )
        )
        cycle = LLMTokenAllowanceCycle(
            plan_holder_user_id=user.id,
            allowance_pool="provider_reported_billable_tokens",
            cycle_started_at=now - timedelta(minutes=1),
            cycle_ends_at=now + timedelta(days=30),
            policy_version=1,
            token_limit=100,
            reserved_tokens=32,
            consumed_tokens=0,
        )
        session.add(cycle)
        await session.flush()
        session.add(
            LLMTokenAllowanceReservation(
                dispatch_id=dispatch_id,
                tenant_id=tenant.id,
                actor_user_id=user.id,
                plan_holder_user_id=user.id,
                allowance_pool="provider_reported_billable_tokens",
                cycle_id=cycle.id,
                model_row_id=9101,
                provider_row_id=9102,
                model_id="p21t16-model",
                catalog_id="p21t16-catalog",
                funding_class="platform",
                payer_ref=f"tenant:{tenant.id}",
                credential_version=1,
                consent_version=None,
                policy_version=1,
                reserved_tokens=32,
            )
        )
        session.add(
            LLMDispatchReservationState(
                dispatch_id=dispatch_id,
                status="reserved",
                lease_until=now - timedelta(seconds=1) if lease_expired else now + timedelta(minutes=10),
                actual_tokens=None,
                actual_usd=None,
                latest_event_key=None,
            )
        )
        await session.commit()
        user_id, tenant_id = user.id, tenant.id
    admission = DispatchAdmission(
        dispatch_id=dispatch_id,
        context=DispatchContext(
            actor_user_id=user_id,
            tenant_id=tenant_id,
            plan_holder_user_id=user_id,
            funding_class="platform",
            payer=f"tenant:{tenant_id}",
        ),
        request=DispatchRequest(model=None, purpose="chat", stage="chat", dispatch_id=dispatch_id),
        reserved=True,
        reservation={"receipt_id": dispatch_id},
    )
    return admission, user_id, tenant_id, dispatch_id


@pytest.mark.asyncio
async def test_postgres_settlement_exact_replay_is_idempotent_and_closes_both_legs():
    admission, user_id, tenant_id, dispatch_id = await create_attempt()
    evidence = {
        "accounting_usage": {"prompt_tokens": 7, "completion_tokens": 5},
        "accounting_cost_reported": True,
        "accounting_cost_usd": "0.000000125",
    }
    with tenant_scope(tenant_id):
        service = LLMSettlementService()
        await asyncio.gather(
            service.settle(admission, outcome="succeeded", evidence=evidence),
            service.settle(admission, outcome="succeeded", evidence=evidence),
        )
    async with async_session_maker() as session:
        state = await session.scalar(
            select(LLMDispatchReservationState).where(LLMDispatchReservationState.dispatch_id == dispatch_id)
        )
        cycle = await session.scalar(
            select(LLMTokenAllowanceCycle).where(LLMTokenAllowanceCycle.plan_holder_user_id == user_id)
        )
        day = await session.scalar(select(LLMBudgetDay).where(LLMBudgetDay.tenant_id == tenant_id))
        events = (
            (
                await session.execute(
                    select(LLMDispatchReservationEvent).where(LLMDispatchReservationEvent.dispatch_id == dispatch_id)
                )
            )
            .scalars()
            .all()
        )
        assert state.status == "settled"
        assert state.actual_tokens == 12
        assert state.actual_usd == Decimal("0.000000125")
        assert cycle.reserved_tokens == 0 and cycle.consumed_tokens == 12
        assert day.reserved_usd == Decimal(0) and day.settled_usd == Decimal("0.000000125")
        assert len(events) == 1


@pytest.mark.asyncio
async def test_postgres_unknown_expired_state_reconciles_without_releasing_liability():
    admission, user_id, tenant_id, dispatch_id = await create_attempt(lease_expired=True)
    with tenant_scope(tenant_id):
        service = LLMSettlementService()
        await service.settle(
            admission,
            outcome="unknown",
            evidence={"accounting_usage": {"prompt_tokens": None, "completion_tokens": None}},
        )
        assert await service.reconcile_once(limit=10) >= 1
    async with async_session_maker() as session:
        state = await session.scalar(
            select(LLMDispatchReservationState).where(LLMDispatchReservationState.dispatch_id == dispatch_id)
        )
        cycle = await session.scalar(
            select(LLMTokenAllowanceCycle).where(LLMTokenAllowanceCycle.plan_holder_user_id == user_id)
        )
        day = await session.scalar(select(LLMBudgetDay).where(LLMBudgetDay.tenant_id == tenant_id))
        assert state.status == "unresolved"
        assert state.actual_tokens is None and state.actual_usd is None
        assert cycle.reserved_tokens == 32 and cycle.consumed_tokens == 0
        assert day.reserved_usd == Decimal("1") and day.settled_usd == Decimal(0)
