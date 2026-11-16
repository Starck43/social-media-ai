"""Pure actual-source policy and adapter parity; no settings, DB or provider."""

from copy import deepcopy

import pytest

from tests.shared.task_plan_support import adapter_update, load_plans, plan

__source_only__ = True


def payload(plans):
    return {**plans.apply_task_plan({}, plan(), job_type="analyze"), "period": "week", "other": {"keep": True}}


@pytest.mark.parametrize("target", ["digest", "prune", "learn", "reflect"])
@pytest.mark.parametrize("surface", ["api", "chat", "cli"])
def test_planned_to_legacy_transition_matches_across_adapters(surface, target):
    plans, _ = load_plans()
    original = payload(plans)
    writes = adapter_update(surface, original, {"job_type": target})
    assert len(writes) == 1
    assert writes[0]["job_type"] == target
    assert writes[0]["agent_scenario_id"] is None
    assert writes[0]["payload"] == {"period": "week", "other": {"keep": True}}
    assert "task_plan" in original


@pytest.mark.parametrize("surface", ["api", "chat", "cli"])
def test_non_owned_edit_preserves_canonical_plan_in_each_adapter(surface):
    plans, _ = load_plans()
    original = payload(plans)
    # The available ordinary field shared by all adapters is the unchanged type;
    # it must not clear or reframe the rolling window/scope.
    writes = adapter_update(surface, original, {"job_type": "analyze"})
    assert writes[0]["payload"] == original
    assert writes[0].get("agent_scenario_id", 3) == 3


@pytest.mark.parametrize("target", ["digest", "prune", "learn", "reflect"])
def test_transition_clears_both_stale_scenario_projections(target):
    plans, _ = load_plans()
    result = plans.prepare_task_plan_update(payload(plans), {}, job_type=target)
    assert result.cleared and result.plan is None
    assert result.changes["agent_scenario_id"] is None
    assert "scenario_id" not in result.changes["payload"]


def test_replacement_is_atomic_policy_and_does_not_mutate_input():
    plans, _ = load_plans()
    original = payload(plans)
    saved = deepcopy(original)
    new = plan(source_ids=[22], scenario_id=None, trigger={"type": "schedule", "cron": "0 9 * * *", "timezone": "UTC"})
    patch = {"task_plan": new, "payload": {"other": "new"}}
    result = plans.prepare_task_plan_update(original, patch, job_type="collect")
    assert result.changes["source_ids"] == [22]
    assert result.changes["agent_scenario_id"] is None
    assert result.changes["cron_expr"] == "0 9 * * *"
    assert result.changes["payload"]["other"] == "new"
    assert original == saved and patch["task_plan"] == new


@pytest.mark.parametrize(
    "patch",
    [
        {"source_ids": [22]},
        {"agent_scenario_id": None},
        {"cron_expr": "0 * * * *"},
        {"collection": {}},
        {"start_date": "2026-10-01"},
        {"end_date": "2026-10-02"},
        {"payload": {"task_plan": plan(source_ids=[22])}},
        {"payload": {"collection": {}}},
        {"payload": {"cli_dates": {"start_date": "2026-10-01"}}},
        {"payload": {"scenario_id": 22}},
    ],
)
def test_preserved_plan_refuses_legacy_and_inline_scope_changes(patch):
    plans, _ = load_plans()
    with pytest.raises(ValueError):
        plans.prepare_task_plan_update(payload(plans), patch, job_type="analyze")


def test_explicit_null_cannot_bypass_plan_and_legacy_requires_envelope():
    plans, _ = load_plans()
    with pytest.raises(ValueError, match="cannot be null"):
        plans.prepare_task_plan_update(payload(plans), {"task_plan": None}, job_type="analyze")
    with pytest.raises(ValueError, match="introduce"):
        plans.prepare_task_plan_update({}, {"payload": {"task_plan": plan()}}, job_type="analyze")


def test_explicit_transition_scenario_is_not_stale_inheritance():
    plans, _ = load_plans()
    result = plans.prepare_task_plan_update(payload(plans), {"agent_scenario_id": 9}, job_type="digest")
    assert result.changes["agent_scenario_id"] == 9
    assert "scenario_id" not in result.changes["payload"]


def test_legacy_patch_and_unchanged_plan_fields_keep_their_meaning():
    plans, _ = load_plans()
    patch = {"name": "renamed", "payload": {"period": "day"}}
    assert plans.prepare_task_plan_update({}, patch, job_type="digest").changes == patch
    result = plans.prepare_task_plan_update(
        payload(plans), {"source_ids": [11], "agent_scenario_id": 3, "cron_expr": "@once"}, job_type="analyze"
    )
    assert result.plan.source_ids == (11,)


@pytest.mark.parametrize("surface", ["api", "chat", "cli"])
def test_complete_plan_replacement_has_the_same_scope_and_fk(surface):
    plans, _ = load_plans()
    replacement = plan(source_ids=[22], scenario_id=None)
    writes = adapter_update(surface, payload(plans), {"task_plan": replacement})
    assert len(writes) == 1
    assert writes[0]["agent_scenario_id"] is None
    assert writes[0]["payload"]["task_plan"]["source_ids"] == [22]
    assert "scenario_id" not in writes[0]["payload"]


@pytest.mark.parametrize("bad", [[True], [0], ["11"], (11,)])
def test_noop_relationship_fields_do_not_accept_malformed_id_types(bad):
    plans, _ = load_plans()
    with pytest.raises(ValueError):
        plans.prepare_task_plan_update(payload(plans), {"source_ids": bad}, job_type="analyze")


def test_unknown_job_type_does_not_silently_clear_the_plan():
    plans, _ = load_plans()
    with pytest.raises(ValueError):
        plans.prepare_task_plan_update(payload(plans), {}, job_type="external-send")


@pytest.mark.parametrize("surface", ["api", "chat", "cli"])
def test_transition_schedule_uses_tenant_zone_not_retired_plan_zone(surface):
    plans, _ = load_plans()
    original = plans.apply_task_plan(
        {}, plan(trigger={"type": "schedule", "cron": "0 9 * * *", "timezone": "Europe/Kirov"}), job_type="analyze"
    )
    writes = adapter_update(surface, original, {"job_type": "digest", "cron_expr": "0 10 * * *"})
    assert writes[0]["next_run_at"].zone == "UTC"
    assert "task_plan" not in writes[0]["payload"]
