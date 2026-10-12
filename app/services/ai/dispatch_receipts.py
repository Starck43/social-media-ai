"""Preparation evidence only: a stored receipt is NOT budget admission.

No client calls this boundary yet. Preparation does not prove HTTP dispatch,
provider receipt, charge or success. Admission and terminal events must be
implemented before an acknowledged receipt may authorize a provider request.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4

from .attempt_journal import _tariff_rate

PRICING_POLICY_VERSION = "provider-price-v1"
RECEIPT_FIELDS = (
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
    "tariff_state",
    "pricing_policy_version",
    "source_id",
    "job_id",
    "agent_task_id",
    "agent_session_id",
)
EXECUTION_FIELDS = ("source_id", "job_id", "agent_task_id", "agent_session_id")


def _positive_id(value: Any) -> bool:
    return type(value) is int and 0 < value <= 2147483647


def _tariff_state(input_rate: Decimal | None, output_rate: Decimal | None) -> str:
    if input_rate is None or output_rate is None:
        return "undefined"
    return "free" if input_rate == output_rate == 0 else "known"


def _preparation_time(value: Any) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Preparation time must be timezone-aware")
    return value.astimezone(timezone.utc)


def validate_receipt(record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a fixed, prompt-free record without mutating its caller."""
    if set(record) != set(RECEIPT_FIELDS):
        raise ValueError("Receipt fields do not match the storage contract")
    value = dict(record)
    if type(value["dispatch_id"]) is not str or not re.fullmatch(r"[0-9a-f]{32}", value["dispatch_id"]):
        raise ValueError("Invalid receipt identity")
    for field in ("tenant_id", "llm_model_row_id", "llm_provider_row_id"):
        if not _positive_id(value[field]):
            raise ValueError("A receipt requires explicit payer and model/provider identities")
    for field in EXECUTION_FIELDS:
        if value[field] is not None and not _positive_id(value[field]):
            raise ValueError("Invalid receipt execution reference")
    for field in ("purpose", "stage"):
        if type(value[field]) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", value[field]):
            raise ValueError("Invalid receipt purpose or stage")
    for field in ("model_id", "provider_name"):
        item = value[field]
        if type(item) is not str or not item.strip() or len(item) > 200:
            raise ValueError("Invalid model/provider snapshot")
    value["prepared_at"] = _preparation_time(value["prepared_at"])
    if type(value["utc_day"]) is not date or value["utc_day"] != value["prepared_at"].date():
        raise ValueError("Receipt day must match its UTC preparation time")
    for field in ("input_cost_per_1k", "output_cost_per_1k"):
        rate = value[field]
        if rate is not None and (type(rate) is not Decimal or _tariff_rate(rate) is None):
            raise ValueError("Receipt rates must be exact representable Decimal USD")
    if value["tariff_state"] != _tariff_state(value["input_cost_per_1k"], value["output_cost_per_1k"]):
        raise ValueError("Tariff state does not match its snapshot")
    if value["pricing_policy_version"] != PRICING_POLICY_VERSION:
        raise ValueError("Unsupported receipt pricing policy")
    return value


def build_dispatch_receipt(
    model: Any,
    *,
    tenant_id: int,
    purpose: str,
    stage: str,
    dispatch_id: str,
    prepared_at: datetime,
    execution: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Snapshot the selected DB model once, before any future provider request.

    Invalid/missing rates remain NULL, never coerced to free. A table snapshot
    is only a pricing basis; a later structured provider price still arbitrates
    actual cost. No prompt, credentials, caller/provider payload or token
    estimates are retained here.
    """
    execution = dict(execution or {})
    if set(execution) - set(EXECUTION_FIELDS):
        raise ValueError("Unsupported receipt execution fields")
    prepared_at = _preparation_time(prepared_at)
    provider = model.provider
    input_rate = _tariff_rate(model.input_cost_per_1k)
    output_rate = _tariff_rate(model.output_cost_per_1k)
    record = {
        "dispatch_id": dispatch_id,
        "tenant_id": tenant_id,
        "purpose": purpose,
        "stage": stage,
        "prepared_at": prepared_at,
        "utc_day": prepared_at.date(),
        "llm_model_row_id": model.id,
        "llm_provider_row_id": provider.id,
        "model_id": model.model_id,
        "provider_name": provider.name,
        "input_cost_per_1k": input_rate,
        "output_cost_per_1k": output_rate,
        "tariff_state": _tariff_state(input_rate, output_rate),
        "pricing_policy_version": PRICING_POLICY_VERSION,
        **{field: execution.get(field) for field in EXECUTION_FIELDS},
    }
    return validate_receipt(record)


async def prepare_dispatch_receipt(
    model: Any, *, purpose: str, stage: str, execution: Mapping[str, Any] | None = None, journal: Any = None
) -> Mapping[str, Any]:
    """Store a fresh receipt in the existing owned transaction, NOT admission.

    Payer comes only from explicit trusted tenant scope (operator bypass alone
    is insufficient). A fresh UUID collision or missing commit acknowledgement
    is an error, not permission to reuse the identity for another HTTP request.
    The injection point is for isolated tests; clients are not wired here.
    """
    from app.core.tenant_context import TenantContextError, current_tenant_id

    tenant_id = current_tenant_id()
    if not _positive_id(tenant_id):
        raise TenantContextError("Dispatch receipt requires an explicit payer scope")
    record = build_dispatch_receipt(
        model,
        tenant_id=tenant_id,
        purpose=purpose,
        stage=stage,
        dispatch_id=uuid4().hex,
        prepared_at=datetime.now(timezone.utc),
        execution=execution,
    )
    if journal is None:
        from app.models.managers.llm_dispatch_receipt_manager import LLMDispatchReceiptManager

        journal = LLMDispatchReceiptManager()
    if not await journal.record_receipt(record):
        raise RuntimeError("Fresh receipt identity was already stored")
    return MappingProxyType(record)
