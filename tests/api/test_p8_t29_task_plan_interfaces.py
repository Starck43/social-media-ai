"""Task-plan API contract tests without database access."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1.endpoints.tasks import get_task_result, preview_task_plan, run_task_plan
from app.jobs.source_activity import SourceJobBusy
from app.models.managers.personal_billing_manager import PersonalBillingUnavailable
from app.schemas.task import TaskPlanPreviewRequest, TaskPlanRunRequest

__source_only__ = True


def _plan() -> dict:
    return {
        "version": 1,
        "goal": "Research recent VK posts",
        "platforms": ["vk"],
        "source_scope": "all",
        "source_ids": [],
        "collection": {"queries": ["delivery"]},
        "window": {"type": "rolling", "days": 1},
        "trigger": {"type": "once"},
    }


async def test_preview_returns_canonical_plan_and_collection() -> None:
    response = await preview_task_plan(
        TaskPlanPreviewRequest(job_type="collect", task_plan=_plan()),
        _user=SimpleNamespace(id=1),
    )

    assert response.task_plan["source_scope"] == "all"
    assert response.task_plan["window"] == {"type": "rolling", "days": 1}
    assert response.collection["queries"] == ["delivery"]
    assert response.warnings


@pytest.mark.parametrize("job_type", ["digest", "send", "prune"])
async def test_preview_rejects_job_types_without_social_task_plans(job_type: str) -> None:
    with pytest.raises(HTTPException) as raised:
        await preview_task_plan(
            SimpleNamespace(job_type=job_type, task_plan=_plan()),
            _user=SimpleNamespace(id=1),
        )

    assert raised.value.status_code == 400


async def test_one_off_plan_requires_a_stable_request_id() -> None:
    with pytest.raises(ValidationError):
        TaskPlanRunRequest(task_plan=_plan())


async def test_legacy_api_request_ignores_unknown_transport_fields() -> None:
    request = TaskPlanRunRequest.model_validate(
        {"task_plan": _plan(), "request_id": "req-key-0001", "tenant_id": 999, "provider_error": "private"}
    )

    assert request.request_id == "req-key-0001"
    assert not hasattr(request, "tenant_id")
    assert not hasattr(request, "provider_error")


async def test_result_route_forwards_bounded_run_selection(monkeypatch) -> None:
    observed: dict = {}

    async def read(task_id: int, *, job_id: int | None, limit: int):
        observed.update(task_id=task_id, job_id=job_id, limit=limit)
        return {"task_id": task_id, "analyses": []}

    fake_module = types.ModuleType("app.services.task_results")
    fake_module.task_result = read
    monkeypatch.setitem(sys.modules, "app.services.task_results", fake_module)

    result = await get_task_result(11, job_id=19, limit=3, _user=SimpleNamespace(id=1))

    assert observed == {"task_id": 11, "job_id": 19, "limit": 3}
    assert result == {"task_id": 11, "analyses": []}


async def test_one_off_plan_forwards_request_id_and_returns_launch_receipt(monkeypatch) -> None:
    observed: dict = {}

    async def launch(raw_plan, *, request_id: str, job_type: str):
        observed.update(plan=raw_plan, request_id=request_id, job_type=job_type)
        return {
            "task_id": 17,
            "job_id": 23,
            "status": "queued",
            "job_status": "pending",
            "task_url": "/app/tasks/17",
        }

    fake_module = types.ModuleType("app.services.task_launch")
    fake_module.launch_social_task = launch
    monkeypatch.setitem(sys.modules, "app.services.task_launch", fake_module)
    monkeypatch.setattr("app.core.permissions.has_permission", lambda *_args: True)

    response = await run_task_plan(
        TaskPlanRunRequest(job_type="collect", task_plan=_plan(), request_id="req-key-0001"),
        _user=SimpleNamespace(id=1),
    )

    assert observed == {"plan": _plan(), "request_id": "req-key-0001", "job_type": "collect"}
    assert response["status"] == "queued"
    assert response["task_url"] == "/app/tasks/17"


@pytest.mark.parametrize(
    ("failure", "status_code"),
    [
        ("request_invalid", 400),
        (PermissionError("not permitted"), 403),
        (SourceJobBusy(SimpleNamespace(id=4)), 409),
        (PersonalBillingUnavailable("account not configured"), 409),
    ],
)
async def test_one_off_plan_maps_launch_rejections_to_http(failure, status_code, monkeypatch) -> None:
    from app.services.task_launch import TaskLaunchError

    if failure == "request_invalid":
        failure = TaskLaunchError("request_invalid")

    async def launch(*_args, **_kwargs):
        raise failure

    fake_module = types.ModuleType("app.services.task_launch")
    fake_module.launch_social_task = launch
    monkeypatch.setitem(sys.modules, "app.services.task_launch", fake_module)
    monkeypatch.setattr("app.core.permissions.has_permission", lambda *_args: True)

    with pytest.raises(HTTPException) as raised:
        await run_task_plan(
            TaskPlanRunRequest(job_type="collect", task_plan=_plan(), request_id="req-key-0002"),
            _user=SimpleNamespace(id=1),
        )

    assert raised.value.status_code == status_code


async def test_one_off_plan_maps_typed_error_to_fixed_safe_http_detail(monkeypatch) -> None:
    from app.services.task_launch import TaskLaunchError

    marker = "provider secret must not escape"

    async def launch(*_args, **_kwargs):
        raise TaskLaunchError("source_busy", cause=RuntimeError(marker))

    fake_module = types.ModuleType("app.services.task_launch")
    fake_module.launch_social_task = launch
    monkeypatch.setitem(sys.modules, "app.services.task_launch", fake_module)
    monkeypatch.setattr("app.core.permissions.has_permission", lambda *_args: True)

    with pytest.raises(HTTPException) as raised:
        await run_task_plan(
            TaskPlanRunRequest(task_plan=_plan(), request_id="req-key-0003"),
            _user=SimpleNamespace(id=1),
        )

    assert raised.value.status_code == 409
    assert raised.value.detail == "A source in this plan already has an active job."
    assert marker not in str(raised.value.detail)


async def test_one_off_plan_keeps_unknown_launch_failures_unconfirmed(monkeypatch) -> None:
    marker = "provider credential detail"

    async def launch(*_args, **_kwargs):
        raise RuntimeError(marker)

    fake_module = types.ModuleType("app.services.task_launch")
    fake_module.launch_social_task = launch
    monkeypatch.setitem(sys.modules, "app.services.task_launch", fake_module)
    monkeypatch.setattr("app.core.permissions.has_permission", lambda *_args: True)

    with pytest.raises(HTTPException) as raised:
        await run_task_plan(
            TaskPlanRunRequest(task_plan=_plan(), request_id="req-key-0004"),
            _user=SimpleNamespace(id=1),
        )

    assert raised.value.status_code == 500
    assert raised.value.detail == "The launch result is unconfirmed; inspect the task before retrying."
    assert marker not in str(raised.value.detail)


async def test_one_off_plan_does_not_echo_unclassified_value_errors(monkeypatch) -> None:
    marker = "provider credential detail"

    async def launch(*_args, **_kwargs):
        raise ValueError(marker)

    fake_module = types.ModuleType("app.services.task_launch")
    fake_module.launch_social_task = launch
    monkeypatch.setitem(sys.modules, "app.services.task_launch", fake_module)
    monkeypatch.setattr("app.core.permissions.has_permission", lambda *_args: True)

    with pytest.raises(HTTPException) as raised:
        await run_task_plan(
            TaskPlanRunRequest(task_plan=_plan(), request_id="req-key-0006"),
            _user=SimpleNamespace(id=1),
        )

    assert raised.value.status_code == 500
    assert raised.value.detail == "The launch result is unconfirmed; inspect the task before retrying."
    assert marker not in str(raised.value.detail)


async def test_one_off_plan_maps_task_plan_error_using_its_bounded_code(monkeypatch) -> None:
    from app.services.task_plans import TaskPlanError

    marker = "provider secret"

    async def launch(*_args, **_kwargs):
        raise TaskPlanError(marker)

    fake_module = types.ModuleType("app.services.task_launch")
    fake_module.launch_social_task = launch
    monkeypatch.setitem(sys.modules, "app.services.task_launch", fake_module)
    monkeypatch.setattr("app.core.permissions.has_permission", lambda *_args: True)

    with pytest.raises(HTTPException) as raised:
        await run_task_plan(
            TaskPlanRunRequest(task_plan=_plan(), request_id="req-key-0005"),
            _user=SimpleNamespace(id=1),
        )

    assert raised.value.status_code == 400
    assert raised.value.detail == "Invalid task plan (task_plan_invalid)."
    assert marker not in str(raised.value.detail)
