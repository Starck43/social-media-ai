"""Failure and recovery contracts on the task-owned PostgreSQL ledger."""

import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.core.database import async_session_maker
from app.core.tenant_context import tenant_scope
from app.models import LLMBudgetDay, LLMDispatchReservationState, LLMOutcomeEvent, LLMTokenAllowanceCycle
from app.services.ai.settlement import LLMSettlementService
from tests.ai.test_p21_t16_settlement_pg import create_attempt, isolated_schema


def priced(tokens=12, cost="0.25"):
    return {
        "accounting_usage": {"prompt_tokens": tokens, "completion_tokens": 0},
        "accounting_cost_reported": True,
        "accounting_cost_usd": cost,
    }


async def balances(dispatch_id, tenant_id, user_id):
    async with async_session_maker() as session:
        state = await session.scalar(
            select(LLMDispatchReservationState).where(LLMDispatchReservationState.dispatch_id == dispatch_id)
        )
        day = await session.scalar(select(LLMBudgetDay).where(LLMBudgetDay.tenant_id == tenant_id))
        cycle = await session.scalar(
            select(LLMTokenAllowanceCycle).where(LLMTokenAllowanceCycle.plan_holder_user_id == user_id)
        )
        return (
            state.status,
            state.actual_tokens,
            state.actual_usd,
            day.reserved_usd,
            day.settled_usd,
            cycle.reserved_tokens,
            cycle.consumed_tokens,
        )


@pytest.mark.asyncio
async def test_concurrent_exact_settlement_counts_once():
    admission, user, tenant, dispatch = await create_attempt()
    with tenant_scope(tenant):
        await asyncio.gather(
            *(LLMSettlementService().settle(admission, outcome="succeeded", evidence=priced()) for _ in range(2))
        )
    assert await balances(dispatch, tenant, user) == ("settled", 12, Decimal("0.25"), 0, Decimal("0.25"), 0, 12)


@pytest.mark.asyncio
async def test_conflicting_final_evidence_preserves_recorded_balances():
    admission, user, tenant, dispatch = await create_attempt()
    with tenant_scope(tenant):
        service = LLMSettlementService()
        await service.settle(admission, outcome="succeeded", evidence=priced())
        before = await balances(dispatch, tenant, user)
        with pytest.raises(ValueError, match="Conflicting final"):
            await service.settle(admission, outcome="succeeded", evidence=priced(13, "0.50"))
    assert await balances(dispatch, tenant, user) == before


@pytest.mark.asyncio
async def test_unknown_price_closes_tokens_but_retains_usd_until_known_evidence():
    admission, user, tenant, dispatch = await create_attempt()
    with tenant_scope(tenant):
        service = LLMSettlementService()
        await service.settle(admission, outcome="succeeded", evidence=priced(12, "invalid"))
        assert await balances(dispatch, tenant, user) == ("unresolved", 12, None, Decimal(1), 0, 0, 12)
        await service.settle(admission, outcome="succeeded", evidence=priced())
    assert await balances(dispatch, tenant, user) == ("settled", 12, Decimal("0.25"), 0, Decimal("0.25"), 0, 12)


@pytest.mark.asyncio
async def test_known_usd_missing_tokens_holds_original_pool_across_rollover():
    admission, user, tenant, dispatch = await create_attempt()
    async with async_session_maker() as session:
        cycle = await session.scalar(
            select(LLMTokenAllowanceCycle).where(LLMTokenAllowanceCycle.plan_holder_user_id == user)
        )
        cycle.cycle_started_at -= timedelta(days=31)
        cycle.cycle_ends_at -= timedelta(days=31)
        await session.commit()
    with tenant_scope(tenant):
        await LLMSettlementService().settle(
            admission, outcome="succeeded", evidence={"accounting_cost_reported": True, "accounting_cost_usd": "0.25"}
        )
    assert await balances(dispatch, tenant, user) == ("unresolved", None, Decimal("0.25"), 0, Decimal("0.25"), 32, 0)


@pytest.mark.asyncio
async def test_failure_after_balance_changes_rolls_back_event_and_both_legs(monkeypatch):
    admission, user, tenant, dispatch = await create_attempt()
    service = LLMSettlementService()
    apply_event = service._apply_event

    async def crash(*args, **kwargs):
        await apply_event(*args, **kwargs)
        raise RuntimeError("synthetic crash before commit")

    monkeypatch.setattr(service, "_apply_event", crash)
    before = await balances(dispatch, tenant, user)
    with tenant_scope(tenant), pytest.raises(RuntimeError, match="synthetic crash"):
        await service.settle(admission, outcome="succeeded", evidence=priced())
    assert await balances(dispatch, tenant, user) == before
    async with async_session_maker() as session:
        assert await session.scalar(select(LLMOutcomeEvent.id).where(LLMOutcomeEvent.dispatch_id == dispatch)) is None
    with tenant_scope(tenant):
        await LLMSettlementService().settle(admission, outcome="succeeded", evidence=priced())
    assert (await balances(dispatch, tenant, user))[0] == "settled"


@pytest.mark.asyncio
async def test_expired_orphan_without_event_becomes_explicit_unresolved():
    admission, user, tenant, dispatch = await create_attempt(lease_expired=True)
    with tenant_scope(tenant):
        assert await LLMSettlementService().reconcile_once(limit=100) >= 1
    assert await balances(dispatch, tenant, user) == ("unresolved", None, None, Decimal(1), 0, 32, 0)


@pytest.mark.asyncio
async def test_immutable_evidence_guard_rejects_update_after_cycle():
    admission, _, tenant, dispatch = await create_attempt()
    schema = isolated_schema()
    async with async_session_maker() as session:
        with pytest.raises(Exception, match="immutable|append.only|mutation|UPDATE|update"):
            await session.execute(
                text(f'UPDATE "{schema}".llm_dispatch_receipts SET purpose=purpose WHERE dispatch_id=:dispatch'),
                {"dispatch": dispatch},
            )
        await session.rollback()


@pytest.mark.asyncio
async def test_proven_no_dispatch_releases_both_reservations_once():
    admission, user, tenant, dispatch = await create_attempt()
    with tenant_scope(tenant):
        service = LLMSettlementService()
        await service.settle(admission, outcome="not_dispatched")
        await service.settle(admission, outcome="not_dispatched")
    assert await balances(dispatch, tenant, user) == ("not_dispatched", 0, Decimal(0), 0, 0, 0, 0)


@pytest.mark.asyncio
async def test_above_ceiling_evidence_is_charged_and_freezes_budget():
    admission, user, tenant, dispatch = await create_attempt()
    with tenant_scope(tenant):
        await LLMSettlementService().settle(admission, outcome="succeeded", evidence=priced(40, "2"))
    assert await balances(dispatch, tenant, user) == ("settled", 40, Decimal(2), 0, Decimal(2), 0, 40)
    async with async_session_maker() as session:
        day = await session.scalar(select(LLMBudgetDay).where(LLMBudgetDay.tenant_id == tenant))
        assert day.frozen is True


@pytest.mark.asyncio
async def test_reconcile_consumes_persisted_outcome_after_observer_crash():
    from uuid import uuid4

    from app.models import LLMDispatchReceipt
    from app.services.ai.dispatch_receipts import RECEIPT_FIELDS
    from app.services.ai.outcome_events import build_outcome_event

    admission, user, tenant, dispatch = await create_attempt(lease_expired=True)
    async with async_session_maker() as session:
        receipt = await session.scalar(select(LLMDispatchReceipt).where(LLMDispatchReceipt.dispatch_id == dispatch))
        event = build_outcome_event(
            {field: getattr(receipt, field) for field in RECEIPT_FIELDS},
            event_id=uuid4().hex,
            observed_at=receipt.prepared_at + timedelta(seconds=1),
            dispatch_state="response_received",
            outcome="succeeded",
            accounting_envelope=priced(),
        )
        session.add(LLMOutcomeEvent(**event))
        await session.commit()
    with tenant_scope(tenant):
        assert await LLMSettlementService().reconcile_once(limit=100) >= 1
    # Legacy v1 evidence has no cache dimension or durable aggregate; retain
    # token liability rather than assuming provider prompt counts include caches.
    assert await balances(dispatch, tenant, user) == ("unresolved", None, Decimal("0.25"), 0, Decimal("0.25"), 32, 0)


@pytest.mark.asyncio
async def test_later_unknown_observation_preserves_balances_without_inventing_usage():
    from app.models import LLMDispatchReservationEvent

    admission, user, tenant, dispatch = await create_attempt()
    with tenant_scope(tenant):
        service = LLMSettlementService()
        await service.settle(admission, outcome="succeeded", evidence=priced())
        before = await balances(dispatch, tenant, user)
        await service.settle(admission, outcome="unknown")
    assert await balances(dispatch, tenant, user) == before
    async with async_session_maker() as session:
        unknown_event = await session.scalar(
            select(LLMOutcomeEvent).where(LLMOutcomeEvent.dispatch_id == dispatch, LLMOutcomeEvent.outcome == "unknown")
        )
        observation = await session.scalar(
            select(LLMDispatchReservationEvent).where(
                LLMDispatchReservationEvent.dispatch_id == dispatch,
                LLMDispatchReservationEvent.event_key == unknown_event.event_id,
            )
        )
        assert observation.actual_tokens is None
        assert observation.actual_usd is None
