"""Pure policy only: no database, authorization, or provider I/O."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.services.knowledge.approvals import ApprovalConflict, ApprovalSnapshot, check_decision

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
HASH = "sha256:" + "a" * 64


@pytest.fixture
def snapshot():
    return ApprovalSnapshot(12, HASH, 5, "pending", NOW)


def check(approval, **kwargs):
    args = dict(actor_id=5, current_revision_id=12, current_content_hash=HASH, decision="approved", now=NOW)
    args.update(kwargs)
    check_decision(approval, **args)


@pytest.mark.parametrize("decision", ["approved", "rejected", "revision_requested"])
def test_pending_current_revision_allows_only_policy_step(snapshot, decision):
    check(replace(snapshot, expires_at=NOW + timedelta(seconds=1)), decision=decision)


@pytest.mark.parametrize("status", ["approved", "rejected", "revision_requested", "expired", "unknown"])
def test_existing_decision_cannot_be_replayed_by_policy(snapshot, status):
    with pytest.raises(ApprovalConflict, match="not_pending"):
        check(replace(snapshot, status=status))


def test_expiry_boundary_is_exclusive(snapshot):
    with pytest.raises(ApprovalConflict, match="expired"):
        check(snapshot)


def test_other_actor_is_rejected(snapshot):
    with pytest.raises(ApprovalConflict, match="wrong_approver"):
        check(snapshot, actor_id=6)


@pytest.mark.parametrize("changed", [{"current_revision_id": 13}, {"current_content_hash": "sha256:" + "b" * 64}])
def test_revision_id_and_hash_are_both_bound(snapshot, changed):
    with pytest.raises(ApprovalConflict, match="revision_changed"):
        check(snapshot, **changed)


@pytest.mark.parametrize(
    "changed",
    [
        {"actor_id": True},
        {"current_revision_id": False},
        {"current_content_hash": "bad"},
        {"now": datetime(2026, 11, 9)},
    ],
)
def test_invalid_inputs_fail_closed(snapshot, changed):
    with pytest.raises(ApprovalConflict, match="invalid_snapshot"):
        check(snapshot, **changed)


@pytest.mark.parametrize(
    "changed",
    [
        {"revision_id": 0},
        {"approver_id": True},
        {"content_hash": "sha256:" + "A" * 64},
        {"expires_at": datetime(2026, 11, 9)},
    ],
)
def test_invalid_stored_snapshot_fails_closed(snapshot, changed):
    with pytest.raises(ApprovalConflict, match="invalid_snapshot"):
        check(replace(snapshot, **changed))


def test_unrecognized_decision_is_rejected(snapshot):
    with pytest.raises(ApprovalConflict, match="invalid_decision"):
        check(snapshot, decision="pending")
