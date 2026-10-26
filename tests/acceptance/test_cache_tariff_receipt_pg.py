"""Opt-in PostgreSQL acceptance for the cache-tariff receipt snapshot.

Covers the acceptance criteria of the cache-tariff task that no offline test
can prove:

* the `cache-tariff-v2` snapshot (three cache rates plus the explicit token
  unit) survives a real write and read-back, so a later tariff change cannot
  rewrite the stored basis;
* re-recording the same dispatch identity replays the frozen evidence instead
  of overwriting it, and changed evidence is refused.

The receipt is a snapshot taken from the source model at preparation time; no
catalog row is mutated by these checks. The database triggers installed by the
cache-tariff migration are the mechanism, so the checks run against
PostgreSQL, not against a double.

These immutable receipt rows are retained: no schema setup, migration, reset or
row cleanup occurs here. Synthetic rows carry the `ctp-model` / `ctp-provider`
marker, every test inserts a bounded number of them, and each receipt gets a
valid random 32-hex dispatch identity. Run sequentially, only after the owner
authorizes the DB scope.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.tenant_context import tenant_scope
from app.models import LLMDispatchReceipt
from app.models.managers.llm_dispatch_receipt_manager import (
    LLMDispatchReceiptManager,
    ReceiptEvidenceConflict,
)
from app.services.ai.cache_pricing import (
    cache_price_usd,
    normalize_cache_usage,
    validate_cache_usage,
)
from app.services.ai.dispatch_receipts import build_dispatch_receipt

D = Decimal


class _Provider:
    def __init__(self, provider_id: int, name: str) -> None:
        self.id = provider_id
        self.name = name


class _Model:
    """Minimal stand-in for the rate columns `build_dispatch_receipt` reads."""

    def __init__(self, provider: _Provider, **rates: Decimal | int | None) -> None:
        self.id = rates.pop("row_id")
        self.model_id = "ctp-model"
        self.provider = provider
        self.input_cost_per_1k = rates.pop("input_cost_per_1k")
        self.output_cost_per_1k = rates.pop("output_cost_per_1k")
        self.cache_read_cost_per_1m = rates.pop("cache_read_cost_per_1m")
        self.cache_write_5m_cost_per_1m = rates.pop("cache_write_5m_cost_per_1m")
        self.cache_write_1h_cost_per_1m = rates.pop("cache_write_1h_cost_per_1m")
        self.pricing_token_unit = rates.pop("pricing_token_unit")


def _ordered_model() -> _Model:
    return _Model(
        _Provider(9001, "ctp-provider"),
        row_id=9001,
        input_cost_per_1k=D("0.003"),
        output_cost_per_1k=D("0.015"),
        cache_read_cost_per_1m=D("0.30"),
        cache_write_5m_cost_per_1m=D("3.75"),
        cache_write_1h_cost_per_1m=D("6.00"),
        pricing_token_unit=1_000_000,
    )


def _receipt(model: _Model, tenant_id: int, dispatch_id: str | None = None):
    return build_dispatch_receipt(
        model,
        tenant_id=tenant_id,
        purpose="analysis",
        stage="prepare",
        dispatch_id=dispatch_id or uuid4().hex,
        prepared_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def tenant_id():
    """Explicit payer; the manager refuses an implicit or bootstrap payer."""
    return 1


@pytest.mark.asyncio
async def test_cache_tariff_snapshot_survives_write_and_read_back(tenant_id):
    model = _ordered_model()
    record = _receipt(model, tenant_id)

    with tenant_scope(tenant_id):
        created = await LLMDispatchReceiptManager().record_receipt(record)
    assert created is True

    stored = await LLMDispatchReceipt.objects.filter(dispatch_id=record["dispatch_id"]).first()
    assert stored is not None
    assert stored.pricing_policy_version == "cache-tariff-v2"
    assert stored.pricing_token_unit == 1_000_000
    assert stored.cache_read_cost_per_1m == D("0.30")
    assert stored.cache_write_5m_cost_per_1m == D("3.75")
    assert stored.cache_write_1h_cost_per_1m == D("6.00")
    assert stored.tariff_state == "known"


@pytest.mark.asyncio
async def test_model_reprice_does_not_rewrite_a_stored_snapshot(tenant_id):
    """The stored basis is the source tariff at preparation time, not today's."""
    model = _ordered_model()
    record = _receipt(model, tenant_id)

    with tenant_scope(tenant_id):
        assert await LLMDispatchReceiptManager().record_receipt(record) is True

    # The source model changes afterwards; no catalog row is written here.
    model.cache_read_cost_per_1m = D("9.99")
    model.cache_write_5m_cost_per_1m = D("9.99")
    model.cache_write_1h_cost_per_1m = D("9.99")

    stored = await LLMDispatchReceipt.objects.filter(dispatch_id=record["dispatch_id"]).first()
    assert stored is not None
    assert stored.cache_read_cost_per_1m == D("0.30")
    assert stored.cache_write_5m_cost_per_1m == D("3.75")
    assert stored.cache_write_1h_cost_per_1m == D("6.00")
    assert stored.pricing_token_unit == 1_000_000
    assert stored.tariff_state == "known"


@pytest.mark.asyncio
async def test_replaying_the_same_identity_keeps_frozen_evidence(tenant_id):
    model = _ordered_model()
    dispatch_id = uuid4().hex
    first = _receipt(model, tenant_id, dispatch_id)

    with tenant_scope(tenant_id):
        manager = LLMDispatchReceiptManager()
        assert await manager.record_receipt(first) is True
        # Replaying the identical stored record is idempotent, not a second row.
        assert await manager.record_receipt(dict(first)) is False

    rows = await LLMDispatchReceipt.objects.filter(dispatch_id=dispatch_id)
    assert len(rows) == 1


_REPLAY_FIELD_VALUES = (
    ("cache_read_cost_per_1m", D("1.11")),
    ("cache_write_5m_cost_per_1m", D("4.44")),
    ("cache_write_1h_cost_per_1m", D("7.77")),
    ("pricing_token_unit", 1000),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("field", "changed"), _REPLAY_FIELD_VALUES)
async def test_replay_under_changed_evidence_is_refused(tenant_id, field, changed):
    model = _ordered_model()
    dispatch_id = uuid4().hex
    first = _receipt(model, tenant_id, dispatch_id)
    # Replay the identical dispatch identity, copying the original receipt and
    # changing exactly one tariff field: the stored evidence wins and the
    # conflicting record is refused.
    conflicting = dict(first)
    conflicting[field] = changed
    # Every receipt field except the single parameterized one is unchanged, so
    # the refusal can only be attributed to that field.
    assert {k: v for k, v in conflicting.items() if k != field} == {
        k: v for k, v in first.items() if k != field
    }
    assert conflicting[field] == changed
    assert first[field] != changed

    with tenant_scope(tenant_id):
        manager = LLMDispatchReceiptManager()
        assert await manager.record_receipt(first) is True
        with pytest.raises(ReceiptEvidenceConflict):
            await manager.record_receipt(conflicting)

    rows = await LLMDispatchReceipt.objects.filter(dispatch_id=dispatch_id)
    assert len(rows) == 1
    stored = rows[0]
    assert getattr(stored, field) == first[field]
    # The refused replay left the rest of the frozen evidence untouched.
    assert stored.tariff_state == "known"


@pytest.mark.asyncio
async def test_usage_priced_from_frozen_snapshot_matches_original_rates(tenant_id):
    """A usage priced from the frozen snapshot yields the original amount.

    The stored receipt is read-only evidence, so pricing it later reproduces the
    tariff frozen at preparation time. No source-model change is exercised here.
    """
    model = _ordered_model()
    record = _receipt(model, tenant_id)

    with tenant_scope(tenant_id):
        assert await LLMDispatchReceiptManager().record_receipt(record) is True
    stored = await LLMDispatchReceipt.objects.filter(dispatch_id=record["dispatch_id"]).first()
    assert stored is not None

    usage = normalize_cache_usage(
        {
            "usage": {
                "input_tokens": 1000,
                "cache_read_input_tokens": 2_000_000,
                "cache_creation_input_tokens": 0,
            }
        },
        "anthropic",
    )
    assert validate_cache_usage(usage) is not None

    tariff = {
        "input_cost_per_1k": stored.input_cost_per_1k,
        "cache_read_cost_per_1m": stored.cache_read_cost_per_1m,
        "cache_write_5m_cost_per_1m": stored.cache_write_5m_cost_per_1m,
        "cache_write_1h_cost_per_1m": stored.cache_write_1h_cost_per_1m,
    }
    cost = cache_price_usd(
        usage,
        tariff,
        prompt_tokens=1000,
        pricing_unit=stored.pricing_token_unit,
    )
    assert cost is not None
    # The unit is USD per 1M, so 1000 ordinary tokens cost 0.003/1M each and
    # 2M cache reads cost 0.30/1M each.
    assert cost == D(1000) * D("0.003") / 1_000_000 + D(2_000_000) * D("0.30") / 1_000_000
    assert cost == D("0.600003")
