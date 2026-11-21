"""Transport contract and versioned legacy projection coverage."""

import json

import pytest

from app.services.execution_plan_contracts import (
    Binding,
    ExecutionPlanError,
    LegacyEffects,
    map_social_plan,
    parse_execution_plan,
)
from app.services.task_plans import TaskPlanError, parse_task_plan
from tests.shared.execution_plan_payloads import mail_plan

__source_only__ = True


@pytest.mark.parametrize("case", ["collect", "analyze", "digest"])
def test_saved_social_plan_round_trip(case):
    plan = {
        "platforms": ["vk"],
        "source_scope": "selected",
        "source_ids": [101],
        "vk_access": {"mode": "api"},
        "window": {"type": "rolling", "days": 7},
        "trigger": {"type": "schedule", "cron": "0 9 * * *", "timezone": "Europe/Kirov"},
    }
    if case != "collect":
        plan["scenario_id"] = 82
    if case == "digest":
        plan.update(
            version=2,
            pipeline={"stages": ["collect", "analyze", "digest"]},
            digest={"window": {"type": "rolling", "days": 30}, "group_by": "themes", "time_breakdown": True},
        )
    binding = Binding(name="vk", capability="vk.posts.read", resource_id=101, vk_access={"mode": "api"})
    mapped = map_social_plan(
        {"task_plan": plan, "collect_before_analyze": case == "analyze"},
        job_type="collect" if case == "collect" else "analyze",
        bindings=(binding,),
        dry_run=True,
        effects=LegacyEffects(rate_limit_per_hour=2, cooldown_seconds=30),
    )
    restored = parse_execution_plan(mapped.model_dump_json())
    assert restored == mapped
    assert parse_task_plan(restored.social_plan) == parse_task_plan(plan)
    assert restored.dry_run and restored.legacy_effects.cooldown_seconds == 30
    assert restored.trigger.timezone == "Europe/Kirov"
    assert [step.operation for step in restored.steps] == {
        "collect": ["social.collect"],
        "analyze": ["social.collect", "social.analyze"],
        "digest": ["social.collect", "social.analyze", "social.digest"],
    }[case]


def test_legacy_empty_does_not_become_explicit_empty_or_unbounded():
    assert map_social_plan({"source_ids": []}, job_type="collect") is None
    with pytest.raises(TaskPlanError, match="task_plan_selected_sources_required"):
        map_social_plan({"task_plan": {"source_scope": "selected", "source_ids": []}}, job_type="collect")
    plan = map_social_plan({"task_plan": {"source_scope": "all", "vk_access": {"mode": "api"}}}, job_type="collect")
    assert not plan.bindings and plan.social_plan["source_scope"] == "all"


@pytest.mark.parametrize(
    "invalid", ["version", "unknown_port", "forward_link", "read_as_write", "scenario", "policy", "window"]
)
def test_invalid_execution_shape_rejected(invalid):
    plan = mail_plan(1, "a" * 64)
    parse_execution_plan(json.dumps(plan))
    if invalid == "version":
        plan["version"] = True
    elif invalid == "unknown_port":
        plan["steps"][1]["inputs"] = {"analysis": ["read"]}
    elif invalid == "forward_link":
        plan["steps"][1]["inputs"] = {"mail": ["save"]}
    elif invalid == "read_as_write":
        plan["steps"][2]["bindings"] = ["inbox"]
    elif invalid == "scenario":
        plan["steps"][1]["scenario_id"] = 1
    elif invalid == "window":
        plan["steps"][0]["window"] = {"type": "fixed", "start": "2026-01-02", "end": "2026-01-01"}
    else:
        plan["steps"][2]["on_failure"] = "continue_partial"
    with pytest.raises(ExecutionPlanError, match="invalid_execution_plan"):
        parse_execution_plan(json.dumps(plan))
