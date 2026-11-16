"""Personal USD and token settlement share one PostgreSQL transaction."""

from __future__ import annotations

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
    LLMDispatchReceipt,
    LLMDispatchReservationEvent,
    LLMDispatchReservationState,
    LLMOutcomeEvent,
    LLMTokenAllowanceCycle,
    LLMTokenAllowanceReservation,
    PersonalBillingAccount,
    PersonalBillingCutover,
    PersonalLLMBudgetDay,
    PersonalLLMDispatch,
    PersonalLLMLedgerEntry,
    PersonalLLMReservation,
    PersonalLLMReservationState,
    PlatformRole,
    Tenant,
    TenantUser,
    User,
    WorkspaceRole,
)
from app.services.ai.dispatch_seam import DispatchAdmission, DispatchContext, DispatchRequest
from app.services.ai.settlement import LLMSettlementService


def _require_isolated_schema() -> None:
    schema = os.environ.get("P21_T16_ISOLATED_SCHEMA")
    if not schema:
        pytest.skip("P21_T16_ISOLATED_SCHEMA must name the task-owned isolated schema")
    if settings.DB_SCHEMA != schema or schema in {"public", "test_schema"}:
        pytest.fail("P21-T16 personal acceptance requires its own non-public, non-shared schema")


async def create_personal_attempt():
    _require_isolated_schema()
    dispatch_id = uuid.uuid4().hex
    suffix = dispatch_id[:12]
    now = datetime.now(timezone.utc)
    day = now.date()
    async with async_session_maker() as session:
        tables = await session.execute(
            text("SELECT to_regclass(:dispatch), to_regclass(:personal_state)"),
            {
                "dispatch": f"{settings.DB_SCHEMA}.personal_llm_dispatches",
                "personal_state": f"{settings.DB_SCHEMA}.personal_llm_reservation_states",
            },
        )
        assert all(tables.one()), "Personal billing prerequisite migrations are required"
        role = await session.scalar(select(PlatformRole).where(PlatformRole.codename == "user"))
        assert role is not None
        user = User(
            username=f"p21t16-personal-{suffix}",
            email=f"p21t16-personal-{suffix}@localhost.invalid",
            hashed_password="synthetic-hash-never-a-credential",
            role_id=role.id,
            is_active=True,
        )
        session.add(user)
        await session.flush()
        tenant = Tenant(
            name=f"P21-T16 personal {suffix}",
            slug=f"p21t16-personal-{suffix}",
            plan="pro",
            plan_holder_user_id=user.id,
            is_active=True,
        )
        session.add(tenant)
        await session.flush()
        owner_role = await session.scalar(select(WorkspaceRole).where(WorkspaceRole.codename == "owner"))
        assert owner_role is not None
        session.add(
            TenantUser(
                tenant_id=tenant.id,
                user_id=user.id,
                role_id=owner_role.id,
                channel="web",
                external_user_id=f"p21t16-personal-{suffix}",
                is_active=True,
            )
        )
        await session.flush()
        account = PersonalBillingAccount(
            user_id=user.id,
            daily_limit_usd=Decimal("10"),
            is_active=True,
            configured_at=now,
            policy_version="personal-daily-cap-v1",
        )
        session.add(account)
        await session.flush()
        reviewer_role = await session.scalar(select(PlatformRole).where(PlatformRole.codename == "admin"))
        assert reviewer_role is not None
        reviewer = User(
            username=f"p21t16-reviewer-{suffix}",
            email=f"p21t16-reviewer-{suffix}@localhost.invalid",
            hashed_password="synthetic-hash-never-a-credential",
            role_id=reviewer_role.id,
            is_active=True,
        )
        session.add(reviewer)
        await session.flush()
        session.add(
            PersonalBillingCutover(
                account_id=account.id,
                effective_at=datetime.combine(day, datetime.min.time(), timezone.utc),
                reviewed_by_user_id=reviewer.id,
                review_version="p21t16-test-clean-midnight-v1",
            )
        )
        await session.flush()
        receipt = LLMDispatchReceipt(
            tenant_id=tenant.id,
            dispatch_id=dispatch_id,
            purpose="chat",
            stage="chat",
            prepared_at=now,
            utc_day=day,
            llm_model_row_id=9201,
            llm_provider_row_id=9202,
            model_id="p21t16-personal-model",
            provider_name="p21t16-personal-provider",
            input_cost_per_1k=None,
            output_cost_per_1k=None,
            cache_read_cost_per_1m=None,
            cache_write_5m_cost_per_1m=None,
            cache_write_1h_cost_per_1m=None,
            pricing_token_unit=1000,
            tariff_state="undefined",
            pricing_policy_version="provider-price-v1",
            source_id=None,
            job_id=None,
            agent_task_id=None,
            agent_session_id=None,
        )
        session.add(receipt)
        await session.flush()
        personal_dispatch = PersonalLLMDispatch(
            **{
                key: getattr(receipt, key)
                for key in (
                    "dispatch_id",
                    "tenant_id",
                    "purpose",
                    "stage",
                    "prepared_at",
                    "utc_day",
                    "llm_model_row_id",
                    "llm_provider_row_id",
                    "model_id",
                    "provider_name",
                    "input_cost_per_1k",
                    "output_cost_per_1k",
                    "cache_read_cost_per_1m",
                    "cache_write_5m_cost_per_1m",
                    "cache_write_1h_cost_per_1m",
                    "pricing_token_unit",
                    "tariff_state",
                    "pricing_policy_version",
                    "source_id",
                    "job_id",
                    "agent_task_id",
                    "agent_session_id",
                )
            },
            account_id=account.id,
            payer_user_id=user.id,
            initiated_by_user_id=user.id,
            credential_version=1,
        )
        session.add(personal_dispatch)
        personal_day = PersonalLLMBudgetDay(
            account_id=account.id,
            utc_day=day,
            settled_usd=Decimal(0),
            reserved_usd=Decimal(0),
            undefined_count=1,
            unbounded_count=0,
            unresolved_count=1,
            frozen=False,
            baseline_version="p21t16-personal-test-v1",
        )
        session.add(personal_day)
        await session.flush()
        personal_reservation = PersonalLLMReservation(
            dispatch_id=dispatch_id,
            account_id=account.id,
            utc_day=day,
            ceiling_state="undefined",
            ceiling_usd=Decimal(0),
            bound_version=None,
            undefined_policy_version="owner-undefined-allow-v1",
            cap_usd=Decimal(10),
        )
        session.add(personal_reservation)
        session.add(
            PersonalLLMReservationState(
                dispatch_id=dispatch_id,
                account_id=account.id,
                utc_day=day,
                status="reserved",
                reserved_usd=Decimal(0),
                settled_usd=Decimal(0),
            )
        )
        await session.flush()
        session.add(
            PersonalLLMLedgerEntry(
                entry_key=uuid.uuid4().hex,
                dispatch_id=dispatch_id,
                account_id=account.id,
                utc_day=day,
                event_id=None,
                entry_kind="reserve",
                reserved_delta_usd=Decimal(0),
                settled_delta_usd=Decimal(0),
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
                model_row_id=9201,
                provider_row_id=9202,
                model_id="p21t16-personal-model",
                catalog_id="p21t16-personal-catalog",
                funding_class="personal",
                payer_ref=f"personal:{user.id}",
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
                lease_until=now + timedelta(minutes=10),
                actual_tokens=None,
                actual_usd=None,
                latest_event_key=None,
            )
        )
        await session.commit()
        tenant_id, user_id, account_id = tenant.id, user.id, account.id
    admission = DispatchAdmission(
        dispatch_id=dispatch_id,
        context=DispatchContext(
            actor_user_id=user_id,
            tenant_id=tenant_id,
            plan_holder_user_id=user_id,
            funding_class="personal",
            payer=f"personal:{user_id}",
        ),
        request=DispatchRequest(model=None, purpose="chat", stage="chat", dispatch_id=dispatch_id),
        reserved=True,
        reservation={"receipt_id": dispatch_id},
    )
    return admission, user_id, tenant_id, account_id, dispatch_id


async def read_personal_balances(user_id: int, tenant_id: int, account_id: int, dispatch_id: str):
    async with async_session_maker() as session:
        token_state = await session.scalar(
            select(LLMDispatchReservationState).where(LLMDispatchReservationState.dispatch_id == dispatch_id)
        )
        personal_state = await session.scalar(
            select(PersonalLLMReservationState).where(PersonalLLMReservationState.dispatch_id == dispatch_id)
        )
        token_cycle = await session.scalar(
            select(LLMTokenAllowanceCycle).where(LLMTokenAllowanceCycle.plan_holder_user_id == user_id)
        )
        day = await session.scalar(select(PersonalLLMBudgetDay).where(PersonalLLMBudgetDay.account_id == account_id))
        return token_state, personal_state, token_cycle, day


@pytest.mark.asyncio
async def test_personal_usd_observer_uses_same_uow_and_exact_replay_is_idempotent():
    admission, user_id, tenant_id, account_id, dispatch_id = await create_personal_attempt()
    evidence = {
        "accounting_usage": {"prompt_tokens": 7, "completion_tokens": 5},
        "accounting_cost_reported": True,
        "accounting_cost_usd": "0.25",
    }
    with tenant_scope(tenant_id):
        service = LLMSettlementService()
        await service.settle(admission, outcome="succeeded", evidence=evidence)
        await service.settle(admission, outcome="succeeded", evidence=evidence)
    token_state, personal_state, cycle, day = await read_personal_balances(user_id, tenant_id, account_id, dispatch_id)
    assert token_state.status == "settled" and token_state.actual_tokens == 12
    assert token_state.actual_usd == Decimal("0.25")
    assert personal_state.status == "settled" and personal_state.settled_usd == Decimal("0.25")
    assert personal_state.reserved_usd == 0
    assert cycle.reserved_tokens == 0 and cycle.consumed_tokens == 12
    assert day.reserved_usd == 0 and day.settled_usd == Decimal("0.25")


@pytest.mark.asyncio
async def test_personal_observer_failure_rolls_back_outcome_token_and_usd(monkeypatch):
    admission, user_id, tenant_id, account_id, dispatch_id = await create_personal_attempt()
    from app.models.managers.personal_llm_billing_manager import PersonalLLMBillingManager

    called = []

    async def fail_after_event(self, *, tenant_id, event_id, session=None):
        called.append((tenant_id, event_id, session is not None))
        raise RuntimeError("synthetic personal ledger failure")

    monkeypatch.setattr(PersonalLLMBillingManager, "observe_event", fail_after_event)
    with tenant_scope(tenant_id), pytest.raises(RuntimeError, match="synthetic personal ledger failure"):
        await LLMSettlementService().settle(
            admission,
            outcome="succeeded",
            evidence={
                "accounting_usage": {"prompt_tokens": 7, "completion_tokens": 5},
                "accounting_cost_reported": True,
                "accounting_cost_usd": "0.25",
            },
        )
    assert called and called[0][0] == tenant_id and called[0][2] is True
    token_state, personal_state, cycle, day = await read_personal_balances(user_id, tenant_id, account_id, dispatch_id)
    assert token_state.status == "reserved" and token_state.actual_tokens is None
    assert personal_state.status == "reserved" and personal_state.settled_usd == 0
    assert cycle.reserved_tokens == 32 and cycle.consumed_tokens == 0
    assert day.reserved_usd == Decimal(0) and day.settled_usd == 0
    async with async_session_maker() as session:
        assert (
            await session.scalar(select(LLMOutcomeEvent.id).where(LLMOutcomeEvent.dispatch_id == dispatch_id)) is None
        )
        assert (
            await session.scalar(
                select(LLMDispatchReservationEvent.id).where(LLMDispatchReservationEvent.dispatch_id == dispatch_id)
            )
            is None
        )
