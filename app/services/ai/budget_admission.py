"""Unwired admission primitives; no provider call, baseline setup or settlement.

Ceilings come from a reviewed internal bound, never caller/provider prose. A
zero UNDEFINED ceiling is an explicit policy exception, not zero actual cost.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Mapping

UNDEFINED_POLICY_VERSION = "owner-undefined-allow-v1"
MAX_USD = Decimal("999999999.999999999")
USD_QUANTUM = Decimal("0.000000001")
PLAN_FIELDS = ("ceiling_state", "ceiling_usd", "bound_version", "undefined_policy_version")
RESERVATION_FIELDS = ("dispatch_id", "tenant_id", "utc_day", *PLAN_FIELDS, "cap_usd")


class BudgetAdmissionDenied(RuntimeError):
    """No committed fresh reservation; never permission to try another client."""


class BudgetAdmissionUnavailable(RuntimeError):
    """Reviewed baseline/schema or trusted bound is unavailable."""


class BudgetAdmissionConflict(RuntimeError):
    """Identity already binds different, standalone or inaccessible evidence."""


def exact_usd(value: Any) -> Decimal:
    """Reject floats, NaN, negatives, overflow and sub-quantum rounding."""
    if type(value) is not Decimal or not value.is_finite() or not 0 <= value <= MAX_USD:
        raise ValueError("Expected finite nonnegative representable Decimal USD")
    if value != value.quantize(USD_QUANTUM):
        raise ValueError("USD must be exactly representable at 1e-9")
    return value


def reservation_plan(
    receipt: Mapping[str, Any], ceiling_usd: Decimal | None, bound_version: str | None
) -> dict[str, Any]:
    """Validate a trusted explicit bound; no token/character-count estimator.

    Known paid tariffs without a reviewed bound can be stored only for an
    unlimited payer, as unbounded liability, never as a zero reservation.
    """
    state = receipt["tariff_state"]
    if ceiling_usd is not None:
        ceiling_usd = exact_usd(ceiling_usd)
    if state == "undefined":
        if ceiling_usd != Decimal(0) or bound_version is not None:
            raise ValueError("UNDEFINED uses the explicit allow policy, not a priced bound")
        ceiling_state = "undefined"
    elif state == "free":
        if ceiling_usd != Decimal(0) or bound_version is not None:
            raise ValueError("Explicit free tariff has a zero basis, not an estimated bound")
        ceiling_state = "bounded"
    elif state == "known":
        if ceiling_usd is None:
            if bound_version is not None:
                raise ValueError("An absent bound cannot carry a bound version")
            ceiling_state = "unbounded"
        else:
            if (
                ceiling_usd <= 0
                or type(bound_version) is not str
                or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", bound_version)
            ):
                raise ValueError("Known paid tariffs require a positive reviewed ceiling and version")
            ceiling_state = "bounded"
    else:
        raise ValueError("Invalid receipt tariff state")
    return dict(ceiling_state=ceiling_state, ceiling_usd=ceiling_usd, bound_version=bound_version,
                undefined_policy_version=UNDEFINED_POLICY_VERSION)


def require_capacity(
    cap: Decimal | None, settled: Decimal, reserved: Decimal, unbounded_count: int, plan: Mapping[str, Any]
) -> None:
    """Owner UNDEFINED exception is separate from known priced capacity."""
    exact_usd(settled)
    exact_usd(reserved)
    amount = plan["ceiling_usd"] or Decimal(0)
    if reserved + amount > MAX_USD:
        raise BudgetAdmissionUnavailable("Reservation balance is not representable")
    if cap is None:
        return
    exact_usd(cap)
    if plan["ceiling_state"] == "undefined":
        return
    if unbounded_count or plan["ceiling_state"] == "unbounded":
        raise BudgetAdmissionDenied("Capped payer requires a reviewed bound and resolved liability")
    if settled + reserved + amount > cap:
        raise BudgetAdmissionDenied("Current payer/day cap would be exceeded")


async def reserve_dispatch_receipt(
    model: Any, *, purpose: str, stage: str, ceiling_usd: Decimal | None,
    bound_version: str | None = None, execution: Mapping[str, Any] | None = None, budget: Any = None,
) -> Mapping[str, Any]:
    """Fresh trusted preparation plus reservation; never called by clients yet.

    Return an immutable receipt only after acknowledged owned commit. Collision,
    cancellation or commit uncertainty is an error, not permission to retry HTTP.
    The reviewed internal bound must cover ALL billable inputs and outputs; this
    slice intentionally supplies no estimator or baseline-provisioning shortcut.
    """
    from datetime import datetime, timezone
    from types import MappingProxyType
    from uuid import uuid4

    from app.core.tenant_context import TenantContextError, current_tenant_id

    from .dispatch_receipts import _positive_id, build_dispatch_receipt

    payer = current_tenant_id()
    if not _positive_id(payer):
        raise TenantContextError("Budget admission requires an explicit payer scope")
    record = build_dispatch_receipt(
        model, tenant_id=payer, purpose=purpose, stage=stage, dispatch_id=uuid4().hex,
        prepared_at=datetime.now(timezone.utc), execution=execution,
    )
    reservation_plan(record, ceiling_usd, bound_version)
    if budget is None:
        from app.models.managers.llm_budget_manager import LLMBudgetManager

        budget = LLMBudgetManager()
    if not await budget.reserve_receipt(record, ceiling_usd=ceiling_usd, bound_version=bound_version):
        raise BudgetAdmissionConflict("Fresh reservation identity was already stored")
    return MappingProxyType(record)
