"""Typed launch boundary contracts shared by HTTP and agent transports."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

__source_only__ = True


def test_launch_argument_schema_is_the_agent_tool_schema() -> None:
    from app.agent.tools import get_tool
    from app.agent.toolset import tasks as _task_tools  # register the tool below
    from app.services.task_contracts import TaskLaunchArguments

    assert _task_tools.task_execute_once is get_tool("task_execute_once").handler
    assert get_tool("task_execute_once").parameters == TaskLaunchArguments.model_json_schema()
    assert set(TaskLaunchArguments.model_json_schema()["properties"]) == {"request_id", "plan"}
    assert TaskLaunchArguments.model_json_schema()["required"] == ["request_id", "plan"]


@pytest.mark.parametrize(
    "payload",
    [
        {"request_id": "request-123"},
        {"plan": {}},
        {"request_id": "request-123", "plan": {}, "tenant_id": 7},
        {"request_id": "ключ-key-123", "plan": {}},
        {"request_id": "request-123\n", "plan": {}},
        {"request_id": True, "plan": {}},
        {"request_id": "short", "plan": {}},
        {"request_id": "r" * 129, "plan": {}},
    ],
)
def test_launch_arguments_reject_missing_extra_and_invalid_request_ids(payload: dict) -> None:
    from app.services.task_contracts import TaskLaunchArguments

    with pytest.raises(ValidationError):
        TaskLaunchArguments.model_validate(payload)


def test_launch_arguments_build_a_fixed_job_command() -> None:
    from app.services.task_contracts import TaskLaunchArguments, TaskLaunchCommand

    request = TaskLaunchArguments.model_validate({"request_id": "request-123", "plan": {"version": 1}})

    assert request.to_command() == TaskLaunchCommand(request_id="request-123", plan={"version": 1}, job_type="analyze")


def test_launch_result_presenter_returns_only_the_transport_allowlist() -> None:
    from app.services.task_contracts import TaskLaunchResult, present_task_launch_result

    result = TaskLaunchResult(
        task_id=17,
        job_id=None,
        status="queued",
        job_status="unknown",
    )

    assert present_task_launch_result(result) == {
        "task_id": 17,
        "job_id": None,
        "status": "queued",
        "job_status": "unknown",
        "task_url": "/app/tasks/17",
    }
    assert "tenant_id" not in present_task_launch_result(result)
    assert "provider_error" not in present_task_launch_result(result)


def test_saved_plan_reader_preserves_legacy_payloads_and_reads_version_one() -> None:
    from app.services.task_plans import read_saved_task_plan

    assert read_saved_task_plan({"collection": {"queries": ["legacy"]}}) is None
    plan = read_saved_task_plan(
        {
            "task_plan": {
                "version": 1,
                "source_scope": "all",
                "collection": {"queries": ["roadwork"]},
                "window": {"type": "rolling", "days": 2},
                "trigger": {"type": "once"},
                "extension": "retained by compatibility reader",
            }
        }
    )
    assert plan is not None
    assert plan.source_scope == "all"
    assert plan.collection.queries == ("roadwork",)
    assert plan.window.days == 2


def test_agent_task_manager_uses_versioned_reader_without_changing_legacy_payloads() -> None:
    from app.models.managers.agent_task_manager import AgentTaskManager
    from app.services.task_plans import apply_task_plan

    legacy = {"collection": {"queries": ["legacy"]}, "custom_setting": True}
    AgentTaskManager._validate_task_plan_binding("collect", legacy, "@once", None)
    assert legacy["custom_setting"] is True

    raw_plan = {
        "version": 1,
        "source_scope": "all",
        "collection": {"queries": ["typed"]},
        "window": {"type": "rolling", "days": 1},
        "trigger": {"type": "once"},
    }
    payload = apply_task_plan({}, raw_plan, job_type="collect")
    AgentTaskManager._validate_task_plan_binding("collect", payload, "@once", None)

    invalid = {"task_plan": {**raw_plan, "version": 4}}
    with pytest.raises(ValueError, match="task_plan_version_unsupported"):
        AgentTaskManager._validate_task_plan_binding("collect", invalid, "@once", None)


@pytest.mark.parametrize(
    ("plan", "expected_code"),
    [
        ({"version": 2}, "task_plan_version_unsupported"),
        (
            {
                "version": 1,
                "source_scope": "all",
                "collection": {"queries": [f"q{i}" for i in range(6)]},
                "window": {"type": "rolling", "days": 1},
                "trigger": {"type": "once"},
            },
            "task_plan_query_limit_exceeded",
        ),
        (
            {
                "version": 1,
                "source_scope": "all",
                "collection": {},
                "window": {"type": "rolling", "days": 1},
                "trigger": {"type": "schedule", "cron": "0 9 * * *", "timezone": "Not/AZone"},
            },
            "task_plan_timezone_invalid",
        ),
    ],
)
def test_saved_plan_errors_have_stable_codes_without_echoing_unknown_fields(plan: dict, expected_code: str) -> None:
    from app.services.task_plans import TaskPlanError, read_saved_task_plan

    marker = "private-provider-detail-must-not-escape"
    try:
        read_saved_task_plan({"task_plan": {**plan, "unexpected": marker}})
    except TaskPlanError as error:
        assert error.code == expected_code
        assert marker not in str(error)
    else:
        pytest.fail(f"expected task-plan error {expected_code}")


def test_task_launch_error_uses_fixed_safe_message() -> None:
    from app.services.task_contracts import TaskLaunchError, task_launch_tool_error

    marker = "provider credential leaked"
    error = TaskLaunchError("billing_unavailable", cause=RuntimeError(marker))

    assert error.code == "billing_unavailable"
    assert error.status_code == 409
    assert error.safe_message
    assert marker not in error.safe_message
    assert marker not in str(error)
    tool_result = task_launch_tool_error(error.code)
    assert tool_result == {"error": error.safe_message, "code": error.code}
    with pytest.raises(ValueError) as unknown:
        TaskLaunchError(marker)
    assert marker not in str(unknown.value)
    safe_unknown = task_launch_tool_error(marker)
    assert safe_unknown["code"] == "launch_unconfirmed"
    assert marker not in safe_unknown["error"]


def test_dynamic_task_plan_error_codes_are_reduced_before_presentation() -> None:
    from app.services.task_contracts import task_plan_tool_error
    from app.services.task_plans import TaskPlanError

    marker = "provider credential must not be exposed"
    safe = task_plan_tool_error(TaskPlanError(marker).code)

    assert safe == {"error": "Invalid task plan (task_plan_invalid).", "code": "task_plan_invalid"}
    assert marker not in safe["error"]
