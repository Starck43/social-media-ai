"""Pure summaries for validated delivery ledgers; no I/O or authorization."""

from __future__ import annotations

from collections import Counter
from typing import Any


class DeliveryFailure(RuntimeError):
    """Safe error code plus structured outcome; dispatcher must not claim success."""

    def __init__(self, code: str, *, result: dict[str, Any] | None = None, retryable: bool = False):
        super().__init__(code)
        self.result = result or {"status": "blocked", "reason": code}
        self.retryable = retryable


def summarize_delivery(state: dict[str, Any]) -> dict[str, Any]:
    """Summarize an already validated ledger without exposing report/destinations."""
    counts = Counter(p["status"] for target in state["targets"] for p in target["parts"])
    total = sum(counts.values())
    if not total:
        raise ValueError("Empty delivery ledger")
    uncertain = counts["in_flight"] + counts["uncertain"]
    if counts["sent"] == total:
        status = "sent"
    elif uncertain:
        status = "uncertain"
    elif counts["blocked"]:
        status = "blocked"
    elif counts["sent"]:
        status = "partial"
    else:
        status = "failed"
    return {
        "status": status,
        "run_id": state["run_id"],
        "sent_parts": counts["sent"],
        "total_parts": total,
        "uncertain_parts": uncertain,
        "blocked_parts": counts["blocked"],
        "rejected_parts": counts["rejected"],
        "pending_parts": counts["pending"],
        "retryable": bool(counts["pending"] + counts["rejected"]) and not uncertain and not counts["blocked"],
    }
