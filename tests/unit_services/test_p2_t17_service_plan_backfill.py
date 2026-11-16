"""Explicit legacy mapping must never guess holders or merge conflicting tiers."""

from datetime import datetime, timezone

import pytest

from app.services.tenancy.service_plan_backfill import (
    BackfillMappingError,
    HolderPlanSelection,
    HolderSelection,
    LegacyWorkspace,
    validate_mapping,
)

__source_only__ = True
NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)
REFERENCE = "ses_test_p2_t17_explicit_resolution"


def _validate(rows=None, holders=None, plans=None, **overrides):
    arguments = dict(
        workspaces=rows if rows is not None else [LegacyWorkspace(10, "pro", None, (1,))],
        holders=holders if holders is not None else {10: HolderSelection(1, REFERENCE)},
        plans=plans if plans is not None else {1: HolderPlanSelection("pro", 2, True, None)},
        verified_user_ids={1, 2, 99},
        policy_keys={("starter", 2), ("pro", 2), ("business", 2)},
        operator_user_id=99,
        resolved_at=NOW,
    )
    arguments.update(overrides)
    return validate_mapping(**arguments)


def test_explicit_multi_owner_choice_is_preserved_with_both_candidates():
    row = LegacyWorkspace(10, "pro", None, (1, 2))
    result = _validate(
        [row], holders={10: HolderSelection(2, REFERENCE)}, plans={2: HolderPlanSelection("pro", 2, True, None)}
    )
    assert result[0].workspace == row
    assert result[0].holder.holder_user_id == 2
    assert result[0].operator_user_id == 99
    assert result[0].resolved_at == NOW


def test_ownerless_bootstrap_requires_explicit_verified_selection():
    result = _validate([LegacyWorkspace(10, "pro", None, ())])
    assert result[0].workspace.owner_user_ids == ()
    assert result[0].holder.decision_ref == REFERENCE


@pytest.mark.parametrize("holders", [{}, {10: HolderSelection(1, REFERENCE), 11: HolderSelection(2, REFERENCE)}])
def test_incomplete_or_extra_workspace_mapping_is_refused(holders):
    with pytest.raises(BackfillMappingError, match="per workspace"):
        _validate(holders=holders)


def test_duplicate_workspace_snapshot_is_refused():
    row = LegacyWorkspace(10, "pro", None, (1,))
    with pytest.raises(BackfillMappingError, match="per workspace"):
        _validate([row, row])


@pytest.mark.parametrize("reference", ["", " ", "x" * 251, None])
def test_even_single_owner_requires_an_explicit_decision(reference):
    with pytest.raises(BackfillMappingError, match="holder decision"):
        _validate(holders={10: HolderSelection(1, reference)})


def test_a_holder_must_be_a_verified_user():
    with pytest.raises(BackfillMappingError, match="unknown holder"):
        _validate(verified_user_ids={99})


def test_existing_sponsorship_cannot_be_reassigned():
    row = LegacyWorkspace(10, "pro", None, (1, 2), plan_holder_user_id=2)
    with pytest.raises(BackfillMappingError, match="cannot be reassigned"):
        _validate([row])


def test_mapping_cannot_transfer_an_owned_workspace_to_a_nonowner():
    with pytest.raises(BackfillMappingError, match="separate lifecycle"):
        _validate([LegacyWorkspace(10, "pro", None, (2,))])


@pytest.mark.parametrize(
    "plans", [{}, {1: HolderPlanSelection("pro", 2, True, None), 2: HolderPlanSelection("pro", 2, True, None)}]
)
def test_exactly_one_plan_for_each_selected_holder_is_required(plans):
    with pytest.raises(BackfillMappingError, match="per selected holder"):
        _validate(plans=plans)


def test_conflicting_tiers_for_one_holder_are_not_collapsed_to_the_highest():
    rows = [LegacyWorkspace(10, "starter", None, (1,)), LegacyWorkspace(11, "business", None, (1,))]
    holders = {key: HolderSelection(1, REFERENCE) for key in (10, 11)}
    with pytest.raises(BackfillMappingError, match="conflict needs a decision"):
        _validate(rows, holders)
    plan = HolderPlanSelection("pro", 2, True, None, REFERENCE)
    result = _validate(rows, holders, {1: plan})
    assert {item.plan for item in result} == {plan}
    assert {item.workspace.plan for item in result} == {"starter", "business"}


def test_expiry_conflict_also_requires_separate_plan_evidence():
    with pytest.raises(BackfillMappingError, match="conflict needs a decision"):
        _validate([LegacyWorkspace(10, "pro", NOW, (1,))])


@pytest.mark.parametrize(
    "plan",
    [
        HolderPlanSelection("future", 2, True, None),
        HolderPlanSelection("pro", 3, True, None),
        HolderPlanSelection("pro", True, True, None),
        HolderPlanSelection("pro", 2, 1, None),
        HolderPlanSelection("pro", 2, True, NOW.replace(tzinfo=None)),
    ],
)
def test_invalid_plan_is_not_normalized_to_pro(plan):
    with pytest.raises(BackfillMappingError, match="invalid or missing policy"):
        _validate(plans={1: plan})


@pytest.mark.parametrize("operator", [True, 0, -1, 3])
def test_operator_identity_is_verified_not_inferred_from_actor(operator):
    with pytest.raises(BackfillMappingError, match="verified operator"):
        _validate(operator_user_id=operator)


def test_naive_resolution_time_and_legacy_expiry_are_refused():
    with pytest.raises(BackfillMappingError, match="aware timestamp"):
        _validate(resolved_at=NOW.replace(tzinfo=None))
    with pytest.raises(BackfillMappingError, match="invalid legacy"):
        _validate([LegacyWorkspace(10, "pro", NOW.replace(tzinfo=None), (1,))])


def test_independent_holders_keep_independent_plans_and_output_is_stable():
    rows = [LegacyWorkspace(11, "starter", None, (2,)), LegacyWorkspace(10, "pro", None, (1,))]
    holders = {10: HolderSelection(1, REFERENCE), 11: HolderSelection(2, REFERENCE)}
    plans = {1: HolderPlanSelection("pro", 2, True, None), 2: HolderPlanSelection("starter", 2, True, None)}
    result = _validate(rows, holders, plans)
    assert [item.workspace.tenant_id for item in result] == [10, 11]
    assert [item.plan.plan_code for item in result] == ["pro", "starter"]
    assert rows[0].plan == "starter"


def test_invalid_owner_snapshot_is_not_filtered_to_manufacture_a_single_owner():
    with pytest.raises(BackfillMappingError, match="unverified legacy owner"):
        _validate([LegacyWorkspace(10, "pro", None, (1, 3))])
