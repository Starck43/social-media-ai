"""Source-only contracts for the P21-T16 settlement boundary."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.tenant_context import tenant_scope
from app.services.ai import dispatch_seam
from app.services.ai import settlement as settlement_module
from app.services.ai.dispatch_seam import DispatchAdmission, DispatchContext, DispatchFoundation

__source_only__ = True


@pytest.mark.asyncio
async def test_reserved_seam_settlement_uses_default_service_even_when_flag_is_off(monkeypatch):
    """An admitted attempt must still account after the feature flag rolls back."""
    admission = DispatchAdmission(
        dispatch_id="a" * 32,
        context=DispatchContext(tenant_id=9),
        request=SimpleNamespace(),
        reserved=True,
        reservation={"receipt_id": "a" * 32},
    )
    service = AsyncMock()
    monkeypatch.setattr(settlement_module, "LLMSettlementService", lambda: service)
    monkeypatch.setattr("app.services.ai.rate_limits.RateAcquirer", lambda: SimpleNamespace())
    monkeypatch.setattr(dispatch_seam, "seam_enabled", lambda: False)

    await dispatch_seam.settle(admission, outcome="unknown", evidence={"accounting_usage": {"prompt_tokens": None}})

    service.settle.assert_awaited_once_with(
        admission, outcome="unknown", evidence={"accounting_usage": {"prompt_tokens": None}}
    )


@pytest.mark.asyncio
async def test_settlement_rejects_untrusted_evidence_fields_before_database_access():
    admission = DispatchAdmission(
        dispatch_id="b" * 32,
        context=DispatchContext(tenant_id=9),
        request=SimpleNamespace(),
        reserved=True,
        reservation={},
    )
    with tenant_scope(9), pytest.raises(ValueError, match="accounting evidence only"):
        await settlement_module.LLMSettlementService().settle(
            admission,
            outcome="succeeded",
            evidence={"prompt": "private content"},
        )


@pytest.mark.asyncio
async def test_reconcile_batch_is_bounded_and_rejects_invalid_limits():
    service = settlement_module.LLMSettlementService()
    with pytest.raises(ValueError, match="between 1 and 100"):
        await service.reconcile_once(limit=101)
    with pytest.raises(ValueError, match="between 1 and 100"):
        await service.reconcile_once(limit=0)
