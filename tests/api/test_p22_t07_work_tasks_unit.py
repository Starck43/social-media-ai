"""Contract shape of the canonical task API: routes, DTOs and refusal mapping."""

__source_only__ = True

import asyncio

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1.conventions import BusinessRoute
from app.api.v2 import tasks as api
from app.schemas.work_tasks import (
    DecisionConfirmation,
    DecisionPreview,
    DeleteConfirmation,
    DeletePreview,
    PlanDraft,
    RunReceipt,
    RunReceiptPage,
    WorkTaskActions,
)
from app.services.execution_plan_contracts import ExecutionPlanError
from app.services.planner.tasks import PlannerConflict, PlannerUnavailable
from app.services.task_run_admission import TaskRunAdmissionError

EXPECTED = {
    ("GET", "/tasks"),
    ("GET", "/tasks/{task_id}"),
    ("POST", "/tasks"),
    ("PATCH", "/tasks/{task_id}"),
    ("POST", "/tasks/{task_id}/plan/preview"),
    ("POST", "/tasks/{task_id}/complete"),
    ("POST", "/tasks/{task_id}/close/preview"),
    ("POST", "/tasks/{task_id}/close"),
    ("POST", "/tasks/{task_id}/reopen/preview"),
    ("POST", "/tasks/{task_id}/reopen"),
    ("POST", "/tasks/{task_id}/schedule"),
    ("POST", "/tasks/{task_id}/delete/preview"),
    ("POST", "/tasks/{task_id}/delete"),
    ("POST", "/tasks/{task_id}/runs"),
    ("GET", "/tasks/{task_id}/runs"),
    ("GET", "/tasks/{task_id}/actions"),
    ("POST", "/tasks/{task_id}/decisions/preview"),
    ("POST", "/tasks/{task_id}/decisions"),
    ("GET", "/tasks/legacy/{namespace}/{legacy_id}"),
}


def test_every_route_uses_the_business_contract():
    assert api.router.prefix == "/tasks"
    found = set()
    for route in api.router.routes:
        assert isinstance(route, BusinessRoute)
        assert route.tags == ["Work tasks"]
        assert route.response_model is not None
        for method in route.methods:
            found.add((method, route.path))
    assert found == EXPECTED


def test_run_now_requires_a_named_idempotency_key():
    route = next(route for route in api.router.routes if route.path == "/tasks/{task_id}/runs")
    header = next(parameter for parameter in route.dependant.header_params if parameter.alias == "Idempotency-Key")
    assert header.field_info.is_required() is True
    import inspect

    declared = inspect.signature(api.run_task).parameters["idempotency_key"].default
    assert declared.alias == "Idempotency-Key" and declared.is_required()


@pytest.mark.parametrize(
    "error, status",
    [
        (PlannerUnavailable("gone"), 404),
        (PlannerConflict("stale_version"), 409),
        (PermissionError("denied"), 403),
        (ExecutionPlanError("invalid_plan"), 422),
        (TaskRunAdmissionError("run_unavailable"), 404),
        (TaskRunAdmissionError("invalid_page"), 422),
        (ValueError("invalid_page"), 422),
    ],
)
def test_service_refusals_become_documented_status_codes(error, status):
    async def failing():
        raise error

    with pytest.raises(HTTPException) as caught:
        asyncio.run(api.invoke(failing()))
    assert caught.value.status_code == status
    # The service refusal text never reaches the envelope: only the status code does.
    assert str(error) not in str(caught.value.detail)


def test_run_replay_conflict_is_a_conflict_not_a_bad_request():
    async def failing():
        raise TaskRunAdmissionError("request_key_conflict")

    with pytest.raises(HTTPException) as caught:
        asyncio.run(api._admission(failing()))
    assert caught.value.status_code == 409


def test_actions_contract_accepts_the_service_projection():
    payload = {
        "task_id": 7,
        "version": 3,
        "work_status": "todo",
        "latest_run": None,
        "next_due_at": None,
        "actions": [
            {"reference": "edit", "label": "Редактировать"},
            {"reference": "run", "label": "Запустить", "revision": 2},
            {"reference": "delete", "label": "Удалить", "confirmation_required": True},
        ],
        "pending_decisions": [
            {
                "reference": "local:4",
                "source": "local",
                "label": "Решение по задаче",
                "choices": ["accepted", "rejected"],
                "expires_at": "2026-12-01T10:00:00Z",
                "reason": "Проверить итог",
                "target_revision": 3,
                "href": None,
            }
        ],
    }
    assert WorkTaskActions(**payload).pending_decisions[0].source == "local"
    with pytest.raises(ValidationError):
        WorkTaskActions(**{**payload, "actions": [{"reference": "export", "label": "Выгрузить"}]})


def test_run_receipt_and_page_bound_the_payload():
    receipt = RunReceipt(run_id=1, task_id=2, revision=1, status="pending", replayed=False)
    page = RunReceiptPage(items=[receipt], limit=50, next_before_id=None)
    assert page.items[0].job_id is None
    with pytest.raises(ValidationError):
        RunReceipt(run_id=1, task_id=2, revision=1, job_id=0, status="pending")


def test_confirmations_carry_a_signed_token():
    assert DeleteConfirmation(scope="series", confirmation="x" * 16).scope == "series"
    with pytest.raises(ValidationError):
        DeleteConfirmation(scope="series", confirmation="short")
    assert DecisionConfirmation(confirmation="y" * 20).reason == ""
    assert (
        DecisionPreview(
            reference="command:9",
            outcome="accepted",
            confirmation="z" * 20,
            source="command",
            label="Команда",
            target_revision="a" * 64,
        ).target_revision
        == "a" * 64
    )


def test_plan_draft_is_an_object_not_a_preformatted_string():
    assert PlanDraft(plan={"steps": []}).plan == {"steps": []}
    with pytest.raises(ValidationError):
        PlanDraft(plan="[]")


def test_delete_preview_effects_are_non_negative_counts():
    effects = {
        "scope": "occurrence",
        "occurrences": 1,
        "enabled_schedules": 0,
        "queued_jobs": 0,
        "running_jobs": 0,
        "retained_run_receipts": 2,
        "child_links_hidden": 0,
        "next_occurrence": None,
    }
    assert DeletePreview(effects=effects, confirmation="c" * 20, expires_in=600).effects.occurrences == 1
    with pytest.raises(ValidationError):
        DeletePreview(effects={**effects, "running_jobs": -1}, confirmation="c" * 20, expires_in=600)


@pytest.mark.parametrize(
    "failure, status, tool_code",
    [
        (PlannerUnavailable("gone"), 404, "work_task_unavailable"),
        (PermissionError("denied"), 403, "work_task_denied"),
        (None, 409, "work_task_stale"),
    ],
)
def test_run_translates_plan_lookup_refusals(monkeypatch, failure, status, tool_code):
    from app.agent.toolset import work_tasks as tool

    async def lookup(_task_id):
        if failure is not None:
            raise failure
        return None

    monkeypatch.setattr(api.execution_plan_service, "get_reference", lookup)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api.run_task(1, "key"))
    assert caught.value.status_code == status
    assert asyncio.run(tool.work_task_run(1, "key"))["code"] == tool_code


@pytest.mark.parametrize(
    "case, status, tool_code",
    [
        ("invalid", 404, "work_task_unavailable"),
        ("missing", 404, "work_task_unavailable"),
        ("invisible", 404, "work_task_unavailable"),
        ("denied", 403, "work_task_denied"),
    ],
)
def test_legacy_resolution_translates_map_and_projection_refusals(monkeypatch, case, status, tool_code):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from app.agent.toolset import work_tasks as tool
    from app.core import database
    from app.core.tenant_context import tenant_scope
    from app.services.planner import legacy_migration

    @asynccontextmanager
    async def session_factory():
        yield object()

    async def lookup(*_args, **_kwargs):
        if case == "invalid":
            raise legacy_migration.LegacyIdentityError("invalid_legacy_namespace")
        return None if case == "missing" else SimpleNamespace(id=2)

    async def project(*_args, **_kwargs):
        if case == "denied":
            raise PermissionError("denied")
        raise PlannerUnavailable("gone")

    monkeypatch.setattr(database, "async_session_maker", session_factory)
    monkeypatch.setattr(legacy_migration, "resolve_legacy_task", lookup)
    monkeypatch.setattr(api.task_service, "get", project)
    with tenant_scope(1):
        with pytest.raises(HTTPException) as caught:
            asyncio.run(api.resolve_legacy("planner", 1))
        assert caught.value.status_code == status
        assert asyncio.run(tool.work_task_resolve_legacy("planner", 1))["code"] == tool_code
