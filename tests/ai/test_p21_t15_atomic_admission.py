"""P21-T15 bounded token/USD admission rules; no DB or provider calls."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.ai.atomic_admission import (
    MAX_BIGINT,
    TOKEN_ALLOWANCE_POOL,
    TOKEN_CYCLE,
    AtomicLLMAdmissionService,
    TokenAllowanceExhausted,
    _tenant_usd_ceiling,
    _token_ceiling,
    reserve_token_ceiling,
)
from app.services.ai.budget_admission import BudgetAdmissionDenied, BudgetAdmissionUnavailable
from app.services.ai.dispatch_seam import (
    CODE_TOKEN_ALLOWANCE_EXHAUSTED,
    DispatchContext,
    DispatchDenial,
    DispatchRequest,
)
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


class _Scalars:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class _ExecuteResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return _Scalars(self.values)


class _Transaction:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.session.rolled_back = exc is not None
        return False


class _AdmissionSession(_Session):
    def __init__(self):
        super().__init__()
        self.rolled_back = False

    def begin(self):
        return _Transaction(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def execute(self, _statement):
        return _ExecuteResult([11])


class _SessionFactory:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_):
        return False


@pytest.mark.asyncio
async def test_service_rolls_back_usd_reservation_when_token_pool_is_exhausted(monkeypatch):
    """Exercise the real UOW boundary after its USD primitive has staged a write."""
    import app.services.ai.atomic_admission as admission_module
    from app.models.managers.llm_budget_manager import LLMBudgetManager

    session = _AdmissionSession()
    staged_usd = []
    monkeypatch.setattr(admission_module, "async_session_maker", lambda: _SessionFactory(session))
    plan = SimpleNamespace(
        plan_holder_user_id=11,
        plan_policy_version=5,
        actor_user_id=11,
        policy=SimpleNamespace(token_policy=SimpleNamespace(unit="token", token_limit=10, policy_version=5)),
    )

    async def resolve(*_, **__):
        return plan

    async def reserve_usd(self, record, **_):
        staged_usd.append(record)
        return True

    async def exhaust(_session, **_):
        raise TokenAllowanceExhausted("exhausted after USD reserve")

    monkeypatch.setattr(admission_module, "resolve_holder_context", resolve)
    monkeypatch.setattr(LLMBudgetManager, "reserve_receipt", reserve_usd)
    monkeypatch.setattr(admission_module, "reserve_token_ceiling", exhaust)
    monkeypatch.setattr(
        "app.services.ai.dispatch_receipts.build_dispatch_receipt",
        lambda *_, **__: {"dispatch_id": "a" * 32, "tenant_id": 22, "tariff_state": "known"},
    )
    request = DispatchRequest(
        model=SimpleNamespace(
            context_window_tokens=32,
            max_output_tokens=8,
            provider=SimpleNamespace(id=2),
            id=3,
            model_id="unit-model",
        ),
        purpose="chat",
        stage="chat",
        dispatch_id="a" * 32,
    )
    context = DispatchContext(
        actor_user_id=11,
        tenant_id=22,
        plan_holder_user_id=11,
        catalog_id="1",
        funding_class="platform",
        payer="tenant:22",
        credential_version=3,
        plan_policy_version=5,
    )

    with pytest.raises(DispatchDenial) as denied:
        await AtomicLLMAdmissionService().reserve(request, context)
    assert denied.value.code == CODE_TOKEN_ALLOWANCE_EXHAUSTED
    assert len(staged_usd) == 1
    assert session.rolled_back is True


@pytest.mark.asyncio
@pytest.mark.parametrize("created", [False, None])
async def test_exact_or_ambiguous_receipt_replay_never_returns_reservation(monkeypatch, created):
    import app.services.ai.atomic_admission as admission_module
    from app.models.managers.llm_budget_manager import LLMBudgetManager

    session = _AdmissionSession()
    token_path = []
    monkeypatch.setattr(admission_module, "async_session_maker", lambda: _SessionFactory(session))
    plan = SimpleNamespace(
        plan_holder_user_id=11,
        plan_policy_version=5,
        actor_user_id=11,
        policy=SimpleNamespace(token_policy=SimpleNamespace(unit="token", token_limit=100, policy_version=5)),
    )

    async def resolve(*_, **__):
        return plan

    def receipt(*_, **__):
        return {"dispatch_id": "a" * 32, "tenant_id": 22, "tariff_state": "known"}

    async def existing(*_, **__):
        return created

    async def reserve_tokens(*args, **kwargs):
        token_path.append((args, kwargs))

    monkeypatch.setattr(admission_module, "resolve_holder_context", resolve)
    monkeypatch.setattr(LLMBudgetManager, "reserve_receipt", existing)
    monkeypatch.setattr(admission_module, "reserve_token_ceiling", reserve_tokens)
    monkeypatch.setattr("app.services.ai.dispatch_receipts.build_dispatch_receipt", receipt)
    request = DispatchRequest(
        model=SimpleNamespace(
            context_window_tokens=32,
            max_output_tokens=8,
            provider=SimpleNamespace(id=2),
            id=3,
            model_id="unit-model",
        ),
        purpose="chat",
        stage="chat",
        dispatch_id="a" * 32,
    )
    context = DispatchContext(
        actor_user_id=11,
        tenant_id=22,
        plan_holder_user_id=11,
        catalog_id="1",
        funding_class="platform",
        payer="tenant:22",
        credential_version=3,
        plan_policy_version=5,
    )

    with pytest.raises(BudgetAdmissionDenied, match="Exact receipt replay"):
        await AtomicLLMAdmissionService().reserve(request, context)
    assert token_path == []
    assert session.rolled_back is True


@pytest.mark.asyncio
async def test_workspace_payer_mismatch_fails_before_money_or_token_reservation(monkeypatch):
    import app.services.ai.atomic_admission as admission_module
    from app.models.managers.llm_budget_manager import LLMBudgetManager

    session = _AdmissionSession()
    money_path = []
    token_path = []
    monkeypatch.setattr(admission_module, "async_session_maker", lambda: _SessionFactory(session))
    plan = SimpleNamespace(
        plan_holder_user_id=11,
        plan_policy_version=5,
        actor_user_id=11,
        policy=SimpleNamespace(token_policy=SimpleNamespace(unit="token", token_limit=100, policy_version=5)),
    )

    async def resolve(*_, **__):
        return plan

    async def money(*_, **__):
        money_path.append(True)
        return True

    async def tokens(*_, **__):
        token_path.append(True)

    monkeypatch.setattr(admission_module, "resolve_holder_context", resolve)
    monkeypatch.setattr(LLMBudgetManager, "reserve_receipt", money)
    monkeypatch.setattr(admission_module, "reserve_token_ceiling", tokens)
    request = DispatchRequest(
        model=SimpleNamespace(context_window_tokens=32, max_output_tokens=8),
        purpose="chat",
        stage="chat",
        dispatch_id="b" * 32,
    )
    context = DispatchContext(
        actor_user_id=11,
        tenant_id=22,
        plan_holder_user_id=11,
        funding_class="platform",
        payer="tenant:999",
        credential_version=3,
        plan_policy_version=5,
    )
    with pytest.raises(BudgetAdmissionDenied, match="Workspace payer changed"):
        await AtomicLLMAdmissionService().reserve(request, context)
    assert money_path == []
    assert token_path == []
    assert session.rolled_back is True


class _PersonalDispatchSession(_AdmissionSession):
    def __init__(self, payer_user_id):
        super().__init__()
        self.payer_user_id = payer_user_id
        self.calls = 0

    async def execute(self, _statement):
        self.calls += 1
        if self.calls == 1:
            return _ExecuteResult([11])
        return _ScalarResult(SimpleNamespace(payer_user_id=self.payer_user_id))


@pytest.mark.asyncio
async def test_personal_dispatch_payer_mismatch_fails_closed(monkeypatch):
    import app.services.ai.atomic_admission as admission_module
    from app.models.managers.personal_llm_billing_manager import PersonalLLMBillingManager

    session = _PersonalDispatchSession(payer_user_id=77)
    personal_reservation = []
    monkeypatch.setattr(admission_module, "async_session_maker", lambda: _SessionFactory(session))
    plan = SimpleNamespace(
        plan_holder_user_id=11,
        plan_policy_version=5,
        actor_user_id=11,
        policy=SimpleNamespace(token_policy=SimpleNamespace(unit="token", token_limit=100, policy_version=5)),
    )

    async def resolve(*_, **__):
        return plan

    async def reserve_personal(*_, **kwargs):
        personal_reservation.append(kwargs)
        return {"__credential_version": 3}

    monkeypatch.setattr(admission_module, "resolve_holder_context", resolve)
    monkeypatch.setattr(PersonalLLMBillingManager, "reserve_dispatch", reserve_personal)
    request = DispatchRequest(
        model=SimpleNamespace(
            context_window_tokens=32,
            max_output_tokens=8,
            provider=SimpleNamespace(api_format="openai", name="provider"),
            model_id="model",
        ),
        purpose="chat",
        stage="chat",
        dispatch_id="c" * 32,
    )
    context = DispatchContext(
        actor_user_id=11,
        tenant_id=22,
        plan_holder_user_id=11,
        funding_class="personal",
        payer="personal:88",
        credential_version=3,
        plan_policy_version=5,
    )
    with pytest.raises(BudgetAdmissionDenied, match="Personal payer changed"):
        await AtomicLLMAdmissionService().reserve(request, context)
    assert len(personal_reservation) == 1
    assert personal_reservation[0]["undefined_outside_cap"] is True
    assert session.rolled_back is True


@pytest.mark.asyncio
async def test_unlimited_cycles_are_independent_per_holder():
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    first = await reserve_token_ceiling(
        _Session(),
        holder_user_id=101,
        policy_version=1,
        token_limit=None,
        reserved_tokens=MAX_BIGINT // 2,
        now=now,
    )
    other = await reserve_token_ceiling(
        _Session(),
        holder_user_id=202,
        policy_version=1,
        token_limit=1,
        reserved_tokens=1,
        now=now,
    )
    assert first.plan_holder_user_id == 101
    assert first.token_limit is None
    assert other.plan_holder_user_id == 202
    assert other.reserved_tokens == 1


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
