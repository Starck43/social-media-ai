"""P21-T15 bounded token/USD admission rules; no DB or provider calls."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.ai.atomic_admission import (
    TOKEN_ALLOWANCE_POOL,
    TOKEN_CYCLE,
    TokenAllowanceExhausted,
    _tenant_usd_ceiling,
    _token_ceiling,
    reserve_token_ceiling,
)
from app.services.ai.budget_admission import BudgetAdmissionDenied, BudgetAdmissionUnavailable
from app.services.ai.dispatch_seam import DispatchRequest
from app.services.ai.llm_client import _personal_admission

__source_only__ = True


def _request(**kwargs):
    model = SimpleNamespace(context_window_tokens=32_000, max_output_tokens=4_000)
    return DispatchRequest(model=model, purpose="chat", stage="chat", **kwargs)


def test_token_ceiling_uses_total_context_and_rejects_unbounded_inputs():
    assert _token_ceiling(_request()) == 32_000
    assert _token_ceiling(_request(requested_output_tokens=2_000)) == 32_000
    with pytest.raises(BudgetAdmissionDenied):
        _token_ceiling(_request(requested_output_tokens=4_001))
    with pytest.raises(BudgetAdmissionUnavailable):
        _token_ceiling(_request(has_media=True))
    with pytest.raises(BudgetAdmissionUnavailable):
        _token_ceiling(DispatchRequest(model=SimpleNamespace(max_output_tokens=4_000), purpose="chat", stage="chat"))


def test_usd_ceiling_prices_every_token_at_highest_snapshot_rate():
    receipt = {
        "tariff_state": "known",
        "pricing_policy_version": "cache-tariff-v2",
        "pricing_token_unit": 1_000_000,
        "input_cost_per_1k": Decimal("0.02"),
        "output_cost_per_1k": Decimal("0.12"),
        "cache_read_cost_per_1m": Decimal("0.01"),
        "cache_write_5m_cost_per_1m": Decimal("0.20"),
        "cache_write_1h_cost_per_1m": Decimal("0.10"),
    }
    ceiling, version = _tenant_usd_ceiling(receipt, 32_000)
    assert ceiling == Decimal("0.006400000")
    assert version == "provider-context-window-v1"

    receipt["cache_write_1h_cost_per_1m"] = None
    assert _tenant_usd_ceiling(receipt, 32_000) == (None, None)
    receipt["tariff_state"] = "undefined"
    assert _tenant_usd_ceiling(receipt, 32_000) == (Decimal(0), None)


@pytest.mark.asyncio
async def test_strict_seam_reservation_skips_legacy_second_usd_commit():
    dispatch_id = "a" * 32
    strict = SimpleNamespace(reserved=True, dispatch_id=dispatch_id)
    receipt, returned_id = await _personal_admission(
        None,
        {},
        purpose="chat",
        stage="chat",
        seam_admission=strict,
    )
    assert receipt is None
    assert returned_id == dispatch_id


class _ScalarResult:
    def __init__(self, row):
        self.row = row

    def scalar_one_or_none(self):
        return self.row


class _Session:
    def __init__(self, cycle=None):
        self.cycle = cycle
        self.added = []

    async def execute(self, _statement):
        return _ScalarResult(self.cycle)

    def add(self, row):
        self.added.append(row)
        if row.__class__.__name__ == "LLMTokenAllowanceCycle":
            self.cycle = row

    async def flush(self):
        if self.cycle is not None and self.cycle.id is None:
            self.cycle.id = 1


@pytest.mark.asyncio
async def test_cycle_starts_on_consumption_and_plan_change_preserves_open_use():
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    session = _Session()
    cycle = await reserve_token_ceiling(
        session,
        holder_user_id=11,
        policy_version=3,
        token_limit=100,
        reserved_tokens=20,
        now=now,
    )
    assert cycle.allowance_pool == TOKEN_ALLOWANCE_POOL
    assert cycle.cycle_started_at == now
    assert cycle.cycle_ends_at == now + TOKEN_CYCLE
    assert cycle.reserved_tokens == 20

    cycle.consumed_tokens = 15
    await reserve_token_ceiling(
        session,
        holder_user_id=11,
        policy_version=4,
        token_limit=200,
        reserved_tokens=10,
        now=now + timedelta(days=1),
    )
    assert cycle.policy_version == 4
    assert cycle.token_limit == 200
    assert cycle.consumed_tokens == 15
    assert cycle.reserved_tokens == 30


@pytest.mark.asyncio
async def test_cycle_denies_overshoot_and_starts_fresh_cycle_after_expiry():
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    old = SimpleNamespace(
        id=1,
        plan_holder_user_id=11,
        allowance_pool=TOKEN_ALLOWANCE_POOL,
        cycle_started_at=now - TOKEN_CYCLE,
        cycle_ends_at=now,
        policy_version=3,
        token_limit=10,
        reserved_tokens=4,
        consumed_tokens=5,
    )
    session = _Session(cycle=None)
    # An expired row is not returned by the active-cycle query; a new period
    # starts at the first subsequent consumption with no rollover.
    fresh = await reserve_token_ceiling(
        session,
        holder_user_id=11,
        policy_version=3,
        token_limit=10,
        reserved_tokens=6,
        now=now + timedelta(seconds=1),
    )
    assert fresh.cycle_started_at == now + timedelta(seconds=1)
    assert fresh.consumed_tokens == 0

    existing = SimpleNamespace(
        id=2,
        plan_holder_user_id=11,
        allowance_pool=TOKEN_ALLOWANCE_POOL,
        cycle_started_at=now,
        cycle_ends_at=now + TOKEN_CYCLE,
        policy_version=3,
        token_limit=10,
        reserved_tokens=4,
        consumed_tokens=5,
    )
    with pytest.raises(TokenAllowanceExhausted):
        await reserve_token_ceiling(
            _Session(existing),
            holder_user_id=11,
            policy_version=3,
            token_limit=10,
            reserved_tokens=2,
            now=now + timedelta(days=1),
        )
