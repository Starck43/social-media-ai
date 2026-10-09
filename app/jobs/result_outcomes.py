"""Bounded interpretation of explicitly failed handler results.

Legacy statistics without a status remain statistics, not failure signals.
Declared failures are terminal: replay safety cannot be inferred from a dict.
This is not a general queue lease or billing-attempt ledger contract.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

SAFE_FAILURE_CODES = frozenset(
    {
        "invalid_structured_output",
        "invalid_evidence_reference",
        "invalid_memory_reference",
        "incomplete_structured_output",
        "llm_call_failed",
    }
)


def reported_llm_cost(result: Any) -> float | None:
    """Known reported USD only; preserve zero and do not coerce unknown values."""
    value = result.get("llm_cost") if isinstance(result, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        cost = float(value)
    except OverflowError:
        return None
    return cost if math.isfinite(cost) and cost >= 0 else None


@dataclass(frozen=True)
class ReturnedFailure:
    code: str
    llm_cost: float | None

    def audit_result(self) -> dict:
        """Persist only the failure contract, not arbitrary provider/customer data."""
        return {"status": "failed", "error": self.code, "llm_cost": self.llm_cost}


def returned_failure(result: Any) -> ReturnedFailure | None:
    if not isinstance(result, dict) or result.get("status") != "failed":
        return None
    error = result.get("error")
    code = error if isinstance(error, str) and error in SAFE_FAILURE_CODES else "handler_returned_failure"
    return ReturnedFailure(code=code, llm_cost=reported_llm_cost(result))
