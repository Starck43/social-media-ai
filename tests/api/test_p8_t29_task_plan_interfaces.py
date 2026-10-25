"""Task-plan API contract tests and persisted source-link round trips."""

from __future__ import annotations

import secrets
import sys
import types
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1.endpoints.tasks import get_task_result, preview_task_plan, run_task_plan
from app.core.tenant_context import tenant_scope
from app.jobs.source_activity import SourceJobBusy
from app.models import Platform
from app.models.managers.agent_task_manager import AgentTaskManager
from app.models.managers.personal_billing_manager import PersonalBillingUnavailable
from app.schemas.task import TaskPlanPreviewRequest, TaskPlanRunRequest
from app.tasks.cron import next_run_at
from tests.api.test_api_permissions import (
    _auth_headers,
    _client,
    _drop,
    _platform_value,
    _register_owner,
)


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


async def _vk_platform_id() -> int:
    platform = await Platform.objects.filter(platform_type="vk").first()
    assert platform is not None, "VK platform is seeded"
    return platform.id


async def test_preview_returns_canonical_plan_and_collection() -> None:
    response = await preview_task_plan(
        TaskPlanPreviewRequest(job_type="collect", task_plan=_plan()),
        _user=SimpleNamespace(id=1),
    )

    assert response.task_plan["source_scope"] == "all"
    assert response.task_plan["window"] == {"type": "rolling", "days": 1}
    assert response.collection["queries"] == ["delivery"]
    assert response.warnings


async def test_one_off_plan_requires_a_stable_request_id() -> None:
    with pytest.raises(ValidationError):
        TaskPlanRunRequest(task_plan=_plan())


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
        (ValueError("invalid plan"), 400),
        (PermissionError("not permitted"), 403),
        (SourceJobBusy(SimpleNamespace(id=4)), 409),
        (PersonalBillingUnavailable("account not configured"), 409),
    ],
)
async def test_one_off_plan_maps_launch_rejections_to_http(failure, status_code, monkeypatch) -> None:
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


@pytest.mark.tenancy
async def test_legacy_task_source_links_round_trip_and_survive_omitted_patch() -> None:
    """Legacy API source_ids still write links; omission preserves them on PATCH."""
    async with await _client() as client:
        owner, tenant_id = await _register_owner(client, "P8T29SourceRoundTrip")
        headers = await _auth_headers(client, owner.username)
        source_id = None
        task_id = None
        try:
            source_response = await client.post(
                "/api/v1/sources",
                json={
                    "name": f"task-plan-source-{secrets.token_hex(3)}",
                    "platform_id": await _platform_value(),
                    "source_type": "USER",
                    "external_id": secrets.token_hex(6),
                },
                headers=headers,
            )
            assert source_response.status_code == 201, source_response.text
            source_id = source_response.json()["id"]

            created = await client.post(
                "/api/v1/tasks",
                json={
                    "name": f"source-round-trip-{secrets.token_hex(3)}",
                    "cron_expr": "0 9 * * *",
                    "job_type": "collect",
                    "source_ids": [source_id],
                    "payload": {"round_trip_marker": "kept"},
                },
                headers=headers,
            )
            assert created.status_code == 201, created.text
            task_id = created.json()["id"]
            assert created.json()["payload"]["round_trip_marker"] == "kept"
            with tenant_scope(tenant_id):
                assert await AgentTaskManager().get_sources(task_id) == [source_id]

            explicit = await client.patch(
                f"/api/v1/tasks/{task_id}",
                json={"source_ids": [source_id]},
                headers=headers,
            )
            assert explicit.status_code == 200, explicit.text
            with tenant_scope(tenant_id):
                assert await AgentTaskManager().get_sources(task_id) == [source_id]

            omitted = await client.patch(
                f"/api/v1/tasks/{task_id}",
                json={"name": "renamed-source-round-trip"},
                headers=headers,
            )
            assert omitted.status_code == 200, omitted.text
            with tenant_scope(tenant_id):
                assert await AgentTaskManager().get_sources(task_id) == [source_id]
        finally:
            if task_id is not None:
                await client.delete(f"/api/v1/tasks/{task_id}", headers=headers)
            if source_id is not None:
                await client.delete(f"/api/v1/sources/{source_id}", headers=headers)
            await _drop(owner, tenant_id)


@pytest.mark.tenancy
async def test_plan_source_links_and_plan_payload_stay_in_sync() -> None:
    """Plan-owned sources cannot be changed through the legacy field alone."""
    async with await _client() as client:
        owner, tenant_id = await _register_owner(client, "P8T29PlanSourceSync")
        headers = await _auth_headers(client, owner.username)
        source_id = None
        task_id = None
        try:
            source_response = await client.post(
                "/api/v1/sources",
                json={
                    "name": f"planned-source-{secrets.token_hex(3)}",
                    "platform_id": await _vk_platform_id(),
                    "source_type": "USER",
                    "external_id": secrets.token_hex(6),
                },
                headers=headers,
            )
            assert source_response.status_code == 201, source_response.text
            source_id = source_response.json()["id"]
            plan = {
                "version": 1,
                "goal": "Collect selected source",
                "platforms": ["vk"],
                "source_scope": "selected",
                "source_ids": [source_id],
                "collection": {},
                "window": {"type": "rolling", "days": 1},
                "trigger": {"type": "schedule", "cron": "0 0 * * *", "timezone": "Asia/Yekaterinburg"},
            }
            before = datetime.now(timezone.utc)
            created = await client.post(
                "/api/v1/tasks",
                json={
                    "name": f"planned-source-sync-{secrets.token_hex(3)}",
                    "job_type": "collect",
                    "task_plan": plan,
                    "source_ids": [source_id],
                    "is_active": False,
                },
                headers=headers,
            )
            assert created.status_code == 201, created.text
            task_id = created.json()["id"]
            assert created.json()["payload"]["task_plan"]["trigger"]["timezone"] == "Asia/Yekaterinburg"
            after = datetime.now(timezone.utc)
            candidates = {
                next_run_at("0 0 * * *", "Asia/Yekaterinburg", after=before),
                next_run_at("0 0 * * *", "Asia/Yekaterinburg", after=after),
            }
            actual = datetime.fromisoformat(created.json()["next_run_at"])
            assert min(abs((actual - candidate).total_seconds()) for candidate in candidates) < 60
            with tenant_scope(tenant_id):
                assert await AgentTaskManager().get_sources(task_id) == [source_id]

            diverged = await client.patch(
                f"/api/v1/tasks/{task_id}",
                json={"source_ids": []},
                headers=headers,
            )
            assert diverged.status_code == 400, diverged.text
            with tenant_scope(tenant_id):
                assert await AgentTaskManager().get_sources(task_id) == [source_id]

            payload_update = await client.patch(
                f"/api/v1/tasks/{task_id}",
                json={"payload": {"round_trip_marker": "preserved-plan"}},
                headers=headers,
            )
            assert payload_update.status_code == 200, payload_update.text
            assert payload_update.json()["payload"]["task_plan"]["source_ids"] == [source_id]
            assert payload_update.json()["payload"]["task_plan"]["trigger"]["timezone"] == "Asia/Yekaterinburg"
            assert payload_update.json()["next_run_at"] == created.json()["next_run_at"]
            assert payload_update.json()["payload"]["round_trip_marker"] == "preserved-plan"
            with tenant_scope(tenant_id):
                assert await AgentTaskManager().get_sources(task_id) == [source_id]
        finally:
            if task_id is not None:
                await client.delete(f"/api/v1/tasks/{task_id}", headers=headers)
            if source_id is not None:
                await client.delete(f"/api/v1/sources/{source_id}", headers=headers)
            await _drop(owner, tenant_id)
