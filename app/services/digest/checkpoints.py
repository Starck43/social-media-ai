"""Pure version-1 delivery ledger helpers; not yet wired into the sender.

This module performs no I/O and grants no access. The caller must lock the run,
recheck ownership/bindings, persist in-flight intent BEFORE sending, and persist
acknowledgements afterward. A surviving in-flight intent is NOT safe to replay.
All mutations return a new document for SQLAlchemy JSON change tracking.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from typing import Any

_STATUSES = {"pending", "in_flight", "sent", "rejected", "uncertain", "blocked"}
_TRANSITIONS = {
    "pending": {"in_flight", "blocked"},
    "rejected": {"in_flight", "blocked"},
    "in_flight": {"sent", "rejected", "uncertain", "blocked"},
}


class CheckpointError(ValueError):
    """Unknown, malformed or mismatched history; never assume it is unsent."""


def _require(condition: bool) -> None:
    if not condition:
        raise CheckpointError("Invalid or mismatched digest checkpoint")


def _positive_id(value: Any) -> bool:
    return type(value) is int and value > 0


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _hash(text: str) -> str:
    _require(_text(text))
    return sha256(text.encode("utf-8")).hexdigest()


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def validate_checkpoint(state: Any) -> dict[str, Any]:
    """Strictly validate stored history and return a detached copy.

    NULL/legacy history is rejected, not converted into a fresh delivery plan.
    Unknown versions/fields fail closed; upgrades need an explicit policy.
    """
    _require(isinstance(state, dict))
    _require(set(state) == {"version", "run_id", "tenant_id", "generation", "content_sha256", "splitter", "targets"})
    _require(type(state["version"]) is int and state["version"] == 1)
    _require(_positive_id(state["run_id"]) and _positive_id(state["tenant_id"]))
    _require(_text(state["generation"]) and len(state["generation"]) <= 128)
    _require(_text(state["splitter"]) and len(state["splitter"]) <= 128)
    _require(_is_hash(state["content_sha256"]))
    _require(isinstance(state["targets"], list) and bool(state["targets"]))
    destinations, bindings = set(), set()
    for target in state["targets"]:
        _require(isinstance(target, dict))
        _require(set(target) == {"binding_id", "channel", "destination_id", "parts"})
        _require(_positive_id(target["binding_id"]))
        _require(target["channel"] in ("telegram", "max"))
        destination = target["destination_id"]
        _require(_text(destination) and destination == destination.strip())
        _require(not destination.startswith("@") or destination == destination.lower())
        key = (target["channel"], destination)
        _require(key not in destinations and target["binding_id"] not in bindings)
        destinations.add(key)
        bindings.add(target["binding_id"])
        _require(isinstance(target["parts"], list) and bool(target["parts"]))
        unfinished = False
        for part in target["parts"]:
            _require(isinstance(part, dict) and set(part) == {"sha256", "status", "message_id"})
            _require(_is_hash(part["sha256"]) and isinstance(part["status"], str))
            _require(part["status"] in _STATUSES)
            _require(part["message_id"] is None or _text(part["message_id"]))
            if unfinished:
                _require(part["status"] == "pending")
            if part["status"] == "sent":
                _require(_text(part["message_id"]))
            else:
                _require(part["message_id"] is None)
                unfinished = True
    return deepcopy(state)


def new_checkpoint(
    *, run_id: int, tenant_id: int, generation: str, content: str, splitter: str, targets: list[dict[str, Any]]
) -> dict[str, Any]:
    """Freeze supplied target identities and hashes of already-rendered parts.

    Targets contain binding_id/channel/destination_id and a nonempty list of
    exact part strings. This helper does NOT implement HTML-safe splitting or
    persistence. The caller retains content and a versioned deterministic splitter.
    """
    _require(isinstance(targets, list))
    frozen = []
    for target in targets:
        _require(isinstance(target, dict) and set(target) == {"binding_id", "channel", "destination_id", "parts"})
        _require(isinstance(target["parts"], list))
        frozen.append(
            {
                "binding_id": target["binding_id"],
                "channel": target["channel"],
                "destination_id": target["destination_id"],
                "parts": [{"sha256": _hash(p), "status": "pending", "message_id": None} for p in target["parts"]],
            }
        )
    return validate_checkpoint(
        {
            "version": 1,
            "run_id": run_id,
            "tenant_id": tenant_id,
            "generation": generation,
            "content_sha256": _hash(content),
            "splitter": splitter,
            "targets": frozen,
        }
    )


def load_checkpoint(
    state: Any, *, run_id: int, tenant_id: int, generation: str, content: str, splitter: str
) -> dict[str, Any]:
    """Bind stored history to the exact expected run, workspace and snapshot.

    Identity matching is not authorization; the caller still needs tenant scope.
    """
    checked = validate_checkpoint(state)
    _require(_positive_id(run_id) and _positive_id(tenant_id))
    _require(
        (checked["run_id"], checked["tenant_id"], checked["generation"], checked["content_sha256"], checked["splitter"])
        == (run_id, tenant_id, generation, _hash(content), splitter)
    )
    return checked


def next_parts(state: Any) -> list[tuple[int, int]]:
    """Return at most one known-unsent part per target, in original order.

    In-flight/uncertain/blocked parts stop that target. A different target can
    progress independently. 'rejected' means transport rejection is proven, not
    an arbitrary exception or timeout. No automatic exactly-once claim is made.
    """
    checked = validate_checkpoint(state)
    ready = []
    for target_index, target in enumerate(checked["targets"]):
        for part_index, part in enumerate(target["parts"]):
            if part["status"] == "sent":
                continue
            if part["status"] in {"pending", "rejected"}:
                ready.append((target_index, part_index))
            break
    return ready


def _part(state: dict[str, Any], target_index: int, part_index: int) -> dict[str, Any]:
    _require(type(target_index) is int and 0 <= target_index < len(state["targets"]))
    parts = state["targets"][target_index]["parts"]
    _require(type(part_index) is int and 0 <= part_index < len(parts))
    return parts[part_index]


def verify_part(state: Any, target_index: int, part_index: int, text: str) -> None:
    """Reject changed payload/boundaries before a future transport call."""
    checked = validate_checkpoint(state)
    _require(_part(checked, target_index, part_index)["sha256"] == _hash(text))


def transition(
    state: Any, target_index: int, part_index: int, outcome: str, *, message_id: str | None = None
) -> dict[str, Any]:
    """Copy-on-write transition; persist its result under a run lock.

    An in-flight intent may become blocked only after a proven local refusal
    BEFORE any HTTP request. Never use blocked for a timeout or generic error.
    Sent, uncertain and blocked are terminal here. Operator resolution and
    force/new-generation APIs are deliberately outside this bounded contract.
    """
    checked = validate_checkpoint(state)
    part = _part(checked, target_index, part_index)
    _require(isinstance(outcome, str) and outcome in _TRANSITIONS.get(part["status"], set()))
    _require(all(p["status"] == "sent" for p in checked["targets"][target_index]["parts"][:part_index]))
    _require(_text(message_id) if outcome == "sent" else message_id is None)
    part.update(status=outcome, message_id=message_id)
    return validate_checkpoint(checked)
