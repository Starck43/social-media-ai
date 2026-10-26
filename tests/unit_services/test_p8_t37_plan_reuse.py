"""No-DB actual-source checks for projection/serialization/platform reuse."""

from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from tests.shared.task_plan_support import ROOT, load_plans, plan, source_functions
from tests.source_import_isolation import load_isolated_source

__source_only__ = True


def test_binding_parses_once_and_still_checks_both_none_mismatches():
    plans, _ = load_plans()
    raw = plan()
    payload = plans.apply_task_plan({}, raw, job_type="analyze")
    manager = load_isolated_source(
        "app.models.managers._reuse_task",
        ROOT / "app/models/managers/agent_task_manager.py",
        {
            "app.core.database": NS(with_db_session=lambda f: f),
            "app.core.permissions": NS(require_permission=lambda *a: lambda f: f),
            "app.types": NS(ActionType=NS(CREATE="create", UPDATE="update", DELETE="delete")),
            "app.models.managers.base_manager": NS(BaseManager=object),
            "app.services.task_plans": plans,
        },
    )
    original = plans.parse_task_plan
    plans.parse_task_plan = Mock(wraps=original)
    manager.AgentTaskManager._validate_task_plan_binding("analyze", payload, "@once", 3)
    assert plans.parse_task_plan.call_count == 1
    for scenario in (None, 9):
        with pytest.raises(ValueError, match="scenario"):
            manager.AgentTaskManager._validate_task_plan_binding("analyze", payload, "@once", scenario)
    payload_without_scenario = plans.apply_task_plan({}, plan(scenario_id=None), job_type="analyze")
    with pytest.raises(ValueError, match="scenario"):
        manager.AgentTaskManager._validate_task_plan_binding("analyze", payload_without_scenario, "@once", 3)
    manager.AgentTaskManager._validate_task_plan_binding("analyze", payload_without_scenario, "@once", None)


def test_preview_serializes_only_once():
    import asyncio

    plans, _ = load_plans()
    serialize = Mock(wraps=plans.task_plan_block)
    plans.task_plan_block = serialize
    module = source_functions(
        "app/api/v1/endpoints/tasks.py",
        ["preview_task_plan"],
        {"app.services.task_plans": plans},
        {
            "router": NS(post=lambda *a, **kw: lambda f: f),
            "TaskPlanPreviewResponse": NS,
            "Depends": lambda *a: None,
            "require_model_perm": lambda *a: None,
            "ActionType": NS(CREATE="create"),
        },
    )
    response = asyncio.run(module.preview_task_plan(NS(task_plan=plan(), job_type="collect")))
    assert response.task_plan["source_ids"] == [11]
    assert serialize.call_count == 1


@pytest.mark.parametrize(
    "kind,expected", [("vk", True), ("VK", True), (NS(db_value="vk"), True), ("telegram", False), (None, False)]
)
def test_shared_platform_predicate_matches_admission_and_worker(kind, expected):
    activity = load_isolated_source("_predicate", ROOT / "app/jobs/source_activity.py")
    assert activity.source_in_task_platform_scope(NS(platform=NS(platform_type=kind)), {"vk"}) is expected


def test_typed_projection_is_equivalent_without_reparsing():
    plans, _ = load_plans()
    parsed = plans.parse_task_plan(plan())
    expected = plans.apply_task_plan({"other": True}, plan(), job_type="collect")
    plans.parse_task_plan = Mock(side_effect=AssertionError("typed projection must not reparse"))
    assert plans.apply_parsed_task_plan({"other": True}, parsed, job_type="collect") == expected
