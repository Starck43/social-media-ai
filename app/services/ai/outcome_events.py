"""Append-only normalized price/outcome observations; not budget settlement.

Only client-owned accounting fields are read. Generated text, legacy projection
zeros and current model prices never become evidence. Unknown/timeout/cancel
observations may be followed by reconciliation events, never rewritten.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Mapping

from .cache_pricing import CACHE_RATE_FIELDS, cache_price_usd, validate_cache_usage
from .attempt_journal import COST_SCALE, _tokens
from .dispatch_receipts import validate_receipt

EVENT_FIELDS = (
    "event_id",
    "dispatch_id",
    "tenant_id",
    "event_kind",
    "observed_at",
    "dispatch_state",
    "outcome",
    "prompt_tokens",
    "completion_tokens",
    "cache_usage",
    "provider_price_state",
    "provider_cost_raw",
    "cost_usd",
    "cost_certainty",
    "price_source",
    "pricing_policy_version",
)
MAX_COST = Decimal("999999999.999999999")
OUTCOMES = {"succeeded", "failed_output", "failed_transport", "timeout", "cancelled", "unknown", "not_dispatched"}


def _amount(value: Any) -> tuple[str | None, Decimal | None]:
    """Safe decimal-only provenance plus exact storable USD, never rounded."""
    if type(value) not in (str, int, float, Decimal):
        return None, None
    try:
        raw = str(value)
        if len(raw) > 128:
            return None, None
        amount = Decimal(raw)
        if not amount.is_finite() or amount < 0:
            return None, None
        amount = amount.copy_abs()  # Normalize negative zero, not its magnitude.
        raw = str(amount)
        if len(raw) > 128:
            return None, None
        if amount == 0:
            return "0", Decimal(0)
        if amount > MAX_COST or amount.adjusted() < -9:
            return raw, None
        with localcontext() as context:
            context.prec = 128
            exact = amount.quantize(COST_SCALE)
        return raw, exact if exact == amount else None
    except (InvalidOperation, ValueError, OverflowError):
        return None, None


def _price(receipt, envelope, prompt, completion):
    reported = envelope.get("accounting_cost_reported", False)
    if type(reported) is not bool:
        raise ValueError("Provider-report presence must be client-owned boolean evidence")
    raw, amount = _amount(envelope.get("accounting_cost_usd"))
    if not reported and envelope.get("accounting_cost_usd") is not None:
        raise ValueError("A provider amount cannot be present without report provenance")
    if reported:
        return {
            "provider_price_state": "resolved" if amount is not None else "unresolved",
            "provider_cost_raw": raw,
            "cost_usd": amount,
            "cost_certainty": "undefined" if amount is None else ("free" if amount == 0 else "priced"),
            "price_source": "provider",
        }
    value = {
        "provider_price_state": "absent",
        "provider_cost_raw": None,
        "cost_usd": None,
        "cost_certainty": "undefined",
        "price_source": "receipt_tariff" if receipt["tariff_state"] != "undefined" else "none",
    }
    input_rate, output_rate = receipt["input_cost_per_1k"], receipt["output_cost_per_1k"]
    policy = receipt["pricing_policy_version"]
    cache_usage = validate_cache_usage(envelope.get("accounting_cache_usage"))
    if policy == "cache-tariff-v2":
        rates = (input_rate, output_rate, *(receipt[field] for field in CACHE_RATE_FIELDS))
        if all(rate == 0 for rate in rates):
            return dict(value, cost_usd=Decimal(0), cost_certainty="free")
        if input_rate is None or output_rate is None or (completion is None and output_rate != 0):
            return value
        input_cost = cache_price_usd(
            cache_usage,
            {"input_cost_per_1k": input_rate, **{field: receipt[field] for field in CACHE_RATE_FIELDS}},
            prompt_tokens=prompt,
            pricing_unit=receipt["pricing_token_unit"],
        )
        if input_cost is None:
            return value
        with localcontext() as context:
            context.prec = 64
            cost = input_cost + Decimal(completion or 0) * output_rate / receipt["pricing_token_unit"]
        _, exact = _amount(cost)
        if exact is not None:
            value.update(cost_usd=exact, cost_certainty="priced")
        return value
    if input_rate is None or output_rate is None:
        return value
    if input_rate == output_rate == 0:
        return dict(value, cost_usd=Decimal(0), cost_certainty="free")
    if (prompt is None and input_rate != 0) or (completion is None and output_rate != 0):
        return value
    with localcontext() as context:
        context.prec = 64
        cost = (Decimal(prompt or 0) * input_rate + Decimal(completion or 0) * output_rate) / 1000
    _, exact = _amount(cost)
    if exact is not None:
        value.update(cost_usd=exact, cost_certainty="priced")
    return value


def build_outcome_event(
    receipt: Mapping[str, Any],
    *,
    event_id: str,
    observed_at: datetime,
    dispatch_state: str,
    outcome: str,
    event_kind: str = "observation",
    accounting_envelope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze one observation using the receipt tariff, not a mutable model.

    event_id/time must survive a persistence retry. A new reconciliation gets a
    distinct event_id. Only future trusted transport code may choose dispatch
    state/outcome; provider text cannot prove dispatch or no charge.
    """
    receipt = validate_receipt(receipt)
    if type(event_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", event_id):
        raise ValueError("Invalid event identity")
    if type(event_kind) is not str or event_kind not in {"observation", "reconciliation"}:
        raise ValueError("Invalid event kind")
    if type(observed_at) is not datetime or observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("Observation time must be aware")
    observed_at = observed_at.astimezone(timezone.utc)
    if observed_at < receipt["prepared_at"]:
        raise ValueError("An observation cannot predate its receipt")
    if type(outcome) is not str or outcome not in OUTCOMES:
        raise ValueError("Invalid observation outcome")
    if type(dispatch_state) is not str or dispatch_state not in {"not_dispatched", "possible", "response_received"}:
        raise ValueError("Invalid dispatch state")
    if (dispatch_state == "not_dispatched") != (outcome == "not_dispatched"):
        raise ValueError("No-dispatch state requires explicit no-dispatch outcome")
    if dispatch_state == "possible" and outcome in {"succeeded", "failed_output"}:
        raise ValueError("Output outcomes require a received response")
    envelope = {} if accounting_envelope is None else dict(accounting_envelope)
    usage = envelope.get("accounting_usage")
    usage = usage if isinstance(usage, dict) else {}
    prompt, completion = _tokens(usage.get("prompt_tokens")), _tokens(usage.get("completion_tokens"))
    cache_usage = (
        validate_cache_usage(envelope.get("accounting_cache_usage"))
        if receipt["pricing_policy_version"] == "cache-tariff-v2"
        else None
    )
    pricing = _price(receipt, envelope, prompt, completion)
    if dispatch_state == "not_dispatched":
        if (
            pricing["provider_price_state"] != "absent"
            or prompt is not None
            or completion is not None
            or cache_usage is not None
        ):
            raise ValueError("No-dispatch proof conflicts with provider evidence")
        pricing.update(cost_usd=None, cost_certainty="not_dispatched", price_source="not_dispatched")
    return {
        "event_id": event_id,
        "dispatch_id": receipt["dispatch_id"],
        "tenant_id": receipt["tenant_id"],
        "event_kind": event_kind,
        "observed_at": observed_at,
        "dispatch_state": dispatch_state,
        "outcome": outcome,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cache_usage": cache_usage,
        **pricing,
        "pricing_policy_version": receipt["pricing_policy_version"],
    }


def validate_outcome_event(record: Mapping[str, Any], receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Recompute against the persisted payer-bound receipt before insertion."""
    if set(record) not in (set(EVENT_FIELDS), set(EVENT_FIELDS) - {"cache_usage"}):
        raise ValueError("Outcome fields do not match the storage contract")
    record = dict(record)
    record.setdefault("cache_usage", None)
    if type(record["provider_price_state"]) is not str:
        raise ValueError("Invalid provider report state")
    if record["provider_cost_raw"] is not None and type(record["provider_cost_raw"]) is not str:
        raise ValueError("Provider decimal provenance must be a string")
    if record["cost_usd"] is not None and type(record["cost_usd"]) is not Decimal:
        raise ValueError("Stored cost must be exact Decimal USD")
    if type(record["tenant_id"]) is not int:
        raise ValueError("Invalid event payer")
    value = build_outcome_event(
        receipt,
        event_id=record["event_id"],
        observed_at=record["observed_at"],
        dispatch_state=record["dispatch_state"],
        outcome=record["outcome"],
        event_kind=record["event_kind"],
        accounting_envelope={
            "accounting_usage": {
                "prompt_tokens": record["prompt_tokens"],
                "completion_tokens": record["completion_tokens"],
            },
            "accounting_cache_usage": record["cache_usage"],
            "accounting_cost_reported": record["provider_price_state"] != "absent",
            "accounting_cost_usd": record["provider_cost_raw"],
        },
    )
    if any(value[field] != record[field] for field in EVENT_FIELDS):
        raise ValueError("Outcome evidence differs from the persisted receipt or pricing contract")
    # bool == 0 must not let fabricated zero usage through replay validation.
    for field in ("prompt_tokens", "completion_tokens"):
        if record[field] is not None and type(record[field]) is not int:
            raise ValueError("Stored usage must be an exact integer")
    return value
