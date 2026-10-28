"""API preview, persistence preparation and confirmed delivery contracts."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import tasks as task_api
from app.schemas.task import TaskPlanPreviewRequest
from app.services.task_creation import prepare_task_plan_create
from app.services.task_plans import parse_task_plan, prepare_task_plan_update, task_plan_block

__source_only__ = True


def _plan(*, stages=("collect", "analyze", "digest"), digest_window=None):
    return {
        "version": 2,
        "goal": "Research recent repair posts",
        "platforms": ["vk"],
        "source_scope": "all",
        "source_ids": [],
        "collection": {"queries": ["repair"], "limit": 20},
        "window": {"type": "rolling", "days": 3},
        "scenario_id": 9,
        "trigger": {"type": "schedule", "cron": "0 8 * * *", "timezone": "Europe/Kirov"},
        "pipeline": {"stages": list(stages)},
        "digest": {
            "window": digest_window or {"type": "fixed", "start": "2026-01-03", "end": "2026-01-08"},
            "group_by": "topic_chains",
            "time_breakdown": True,
        },
    }


async def test_api_preview_returns_composed_stages_and_separate_report_window(monkeypatch):
    async def entitlements(_plan):
        return {"allowed": True}

    async def vk_access(_plan):
        return {"checked": False, "reason": "mocked"}

    monkeypatch.setattr(task_api, "_preview_entitlements", entitlements)
    monkeypatch.setattr("app.services.social.vk_task_access.preview_vk_access", vk_access)

    response = await task_api.preview_task_plan(
        TaskPlanPreviewRequest(job_type="analyze", task_plan=_plan()),
        _user=SimpleNamespace(id=7),
    )
    assert response.task_plan["pipeline"]["stages"] == ["collect", "analyze", "digest"]
    assert response.task_plan["window"] == {"type": "rolling", "days": 3}
    assert response.task_plan["digest"]["window"] == {
        "type": "fixed",
        "start": "2026-01-03",
        "end": "2026-01-08",
    }
    assert response.entitlements == {"allowed": True}


async def test_api_preview_rejects_composed_pipeline_as_collect_only():
    with pytest.raises(HTTPException) as raised:
        await task_api.preview_task_plan(
            TaskPlanPreviewRequest(job_type="collect", task_plan=_plan()),
            _user=SimpleNamespace(id=7),
        )
    assert raised.value.status_code == 400


def test_api_create_and_update_preparation_roundtrip_the_canonical_v2_plan():
    raw = _plan(stages=("analyze", "digest"), digest_window={"type": "rolling", "days": 9})
    created = prepare_task_plan_create(
        {"collection": raw["collection"]},
        raw,
        job_type="analyze",
        requested_cron=None,
        requested_scenario_id=None,
        requested_source_ids=None,
        enforce_source_match=False,
    )
    canonical = task_plan_block(parse_task_plan(raw))
    assert created.payload["task_plan"] == canonical
    assert created.payload["collect_before_analyze"] is False

    ordinary_update = prepare_task_plan_update(created.payload, {"name": "renamed"}, job_type="analyze")
    assert ordinary_update.changes["payload"]["task_plan"] == canonical
    assert ordinary_update.changes["payload"]["collect_before_analyze"] is False

    replacement = _plan(
        stages=("collect", "analyze", "digest"),
        digest_window={"type": "fixed", "start": "2026-02-01", "end": "2026-02-05"},
    )
    replaced = prepare_task_plan_update(
        created.payload,
        {"task_plan": replacement},
        job_type="analyze",
    )
    assert replaced.changes["payload"]["task_plan"] == task_plan_block(parse_task_plan(replacement))
    assert replaced.changes["payload"]["collect_before_analyze"] is True


async def test_api_send_requires_explicit_confirmation_and_forwards_selected_run(monkeypatch):
    calls = []
    module = ModuleType("app.services.task_digest_delivery")

    async def send_task_digest(task_id, job_id):
        calls.append((task_id, job_id))
        return {"status": "sent"}

    module.send_task_digest = send_task_digest
    monkeypatch.setitem(sys.modules, "app.services.task_digest_delivery", module)

    with pytest.raises(HTTPException) as raised:
        await task_api.send_composed_task_digest(31, 42, confirmed=False, _user=SimpleNamespace(id=7))
    assert raised.value.status_code == 400
    assert calls == []

    response = await task_api.send_composed_task_digest(31, 42, confirmed=True, _user=SimpleNamespace(id=7))
    assert response == {"status": "sent"}
    assert calls == [(31, 42)]


async def test_api_send_maps_fresh_delivery_permission_denial_to_safe_http(monkeypatch):
    module = ModuleType("app.services.task_digest_delivery")

    async def deny(*_args, **_kwargs):
        raise PermissionError("foreign task, tenant, or report")

    module.send_task_digest = deny
    monkeypatch.setitem(sys.modules, "app.services.task_digest_delivery", module)

    with pytest.raises(HTTPException) as raised:
        await task_api.send_composed_task_digest(31, 42, confirmed=True, _user=SimpleNamespace(id=7))
    assert raised.value.status_code == 403
    assert raised.value.detail == "Report delivery is not permitted"
    assert "foreign task" not in raised.value.detail
