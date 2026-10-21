"""Pure ledger coverage. No transport requests or persisted run updates."""

import json
from copy import deepcopy

import pytest

from app.services.digest.checkpoints import (
    CheckpointError,
    load_checkpoint,
    new_checkpoint,
    next_parts,
    transition,
    validate_checkpoint,
    verify_part,
)


def checkpoint():
    return new_checkpoint(
        run_id=10,
        tenant_id=20,
        generation="generation-1",
        content="frozen report",
        splitter="html-v1",
        targets=[
            {"binding_id": 1, "channel": "telegram", "destination_id": "-100", "parts": ["first", "second"]},
            {"binding_id": 2, "channel": "max", "destination_id": "30", "parts": ["third"]},
        ],
    )


def acknowledge(state, target, part):
    return transition(transition(state, target, part, "in_flight"), target, part, "sent", message_id="receipt")


def test_roundtrip_detached_snapshot():
    state = checkpoint()
    restored = load_checkpoint(
        json.loads(json.dumps(state)),
        run_id=10,
        tenant_id=20,
        generation="generation-1",
        content="frozen report",
        splitter="html-v1",
    )
    assert restored == state
    restored["targets"][0]["parts"][0]["status"] = "blocked"
    assert state["targets"][0]["parts"][0]["status"] == "pending"
    assert "frozen report" not in json.dumps(state)


def test_partial_success_then_known_rejection_resumes_only_unsent():
    initial = checkpoint()
    state = acknowledge(initial, 0, 0)
    state = acknowledge(state, 1, 0)
    state = transition(transition(state, 0, 1, "in_flight"), 0, 1, "rejected")
    assert next_parts(json.loads(json.dumps(state))) == [(0, 1)]
    assert next_parts(initial) == [(0, 0), (1, 0)]
    assert next_parts(acknowledge(state, 0, 1)) == []


@pytest.mark.parametrize("outcome", ["in_flight", "uncertain", "blocked"])
def test_unknown_or_blocked_target_never_automatically_replays(outcome):
    state = checkpoint()
    if outcome == "blocked":
        state = transition(state, 0, 0, outcome)
    else:
        state = transition(state, 0, 0, "in_flight")
        if outcome == "uncertain":
            state = transition(state, 0, 0, outcome)
    assert next_parts(state) == [(1, 0)]
    with pytest.raises(CheckpointError):
        transition(state, 0, 0, "in_flight")


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", 11),
        ("tenant_id", 21),
        ("generation", "new"),
        ("content", "changed"),
        ("splitter", "html-v2"),
        ("run_id", True),
        ("tenant_id", True),
    ],
)
def test_snapshot_or_identity_mismatch_fails_closed(field, value):
    expected = dict(run_id=10, tenant_id=20, generation="generation-1", content="frozen report", splitter="html-v1")
    expected[field] = value
    with pytest.raises(CheckpointError):
        load_checkpoint(checkpoint(), **expected)


@pytest.mark.parametrize("legacy", [None, {}, {"version": 1}, [], "bad"])
def test_legacy_is_not_all_unsent(legacy):
    with pytest.raises(CheckpointError):
        next_parts(legacy)


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", 2),
        ("version", True),
        ("run_id", -1),
        ("tenant_id", 0),
        ("generation", ""),
        ("splitter", ""),
        ("content_sha256", "bad"),
        ("targets", []),
    ],
)
def test_invalid_document_rejected(field, value):
    state = checkpoint()
    state[field] = value
    with pytest.raises(CheckpointError):
        validate_checkpoint(state)


def test_unknown_fields_not_silently_dropped():
    state = checkpoint()
    state["raw_exception"] = "secret"
    with pytest.raises(CheckpointError):
        validate_checkpoint(state)


@pytest.mark.parametrize("mutation", ["duplicate", "binding", "alias", "receipt", "later_sent", "status"])
def test_invalid_target_or_receipt_history_rejected(mutation):
    state = checkpoint()
    first = state["targets"][0]
    if mutation == "duplicate":
        state["targets"].append(deepcopy(first))
    elif mutation == "binding":
        first["binding_id"] = True
    elif mutation == "alias":
        first["destination_id"] = "@MixedCase"
    elif mutation == "receipt":
        first["parts"][0]["message_id"] = "unproven"
    elif mutation == "later_sent":
        first["parts"][1].update(status="sent", message_id="out-of-order")
    else:
        first["parts"][0]["status"] = "unknown"
    with pytest.raises(CheckpointError):
        validate_checkpoint(state)


@pytest.mark.parametrize("target,part", [(-1, 0), (0, -1), (True, 0), (0, True), (9, 0), (0, 9)])
def test_invalid_indices_cannot_update_other_parts(target, part):
    with pytest.raises(CheckpointError):
        transition(checkpoint(), target, part, "in_flight")


def test_cannot_skip_parts_or_fabricate_acknowledgement():
    state = checkpoint()
    with pytest.raises(CheckpointError):
        transition(state, 0, 1, "in_flight")
    with pytest.raises(CheckpointError):
        transition(state, 0, 0, "sent", message_id="fabricated")
    in_flight = transition(state, 0, 0, "in_flight")
    with pytest.raises(CheckpointError):
        transition(in_flight, 0, 0, "sent")
    sent = acknowledge(state, 0, 0)
    with pytest.raises(CheckpointError):
        transition(sent, 0, 0, "in_flight")


def test_payload_hashes_guard_exact_parts():
    state = checkpoint()
    verify_part(state, 0, 0, "first")
    with pytest.raises(CheckpointError):
        verify_part(state, 0, 0, "second")
    with pytest.raises(CheckpointError):
        verify_part(state, 0, 0, "first ")


@pytest.mark.parametrize("duplicate", ["destination", "binding"])
def test_each_binding_and_normalized_destination_must_be_unique(duplicate):
    state = checkpoint()
    target = deepcopy(state["targets"][0])
    if duplicate == "destination":
        target["binding_id"] = 3
    else:
        target["destination_id"] = "-101"
    state["targets"].append(target)
    with pytest.raises(CheckpointError):
        validate_checkpoint(state)


def test_rejected_is_retryable_but_uncertain_has_no_operator_shortcut():
    state = transition(checkpoint(), 0, 0, "in_flight")
    uncertain = transition(state, 0, 0, "uncertain")
    for outcome in ("pending", "rejected", "sent"):
        with pytest.raises(CheckpointError):
            transition(uncertain, 0, 0, outcome, message_id="receipt" if outcome == "sent" else None)
    rejected = transition(state, 0, 0, "rejected")
    assert transition(rejected, 0, 0, "in_flight")["targets"][0]["parts"][0]["status"] == "in_flight"


def test_local_no_request_refusal_can_block_a_persisted_intent():
    state = transition(checkpoint(), 0, 0, "in_flight")
    blocked = transition(state, 0, 0, "blocked")
    assert next_parts(blocked) == [(1, 0)]
    assert blocked["targets"][0]["parts"][0]["message_id"] is None
    with pytest.raises(CheckpointError):
        transition(blocked, 0, 0, "in_flight")
