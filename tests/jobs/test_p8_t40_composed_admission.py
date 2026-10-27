"""P8-T40 PostgreSQL admission evidence for composed plans and schedules.

Requires the approved root-conftest test_schema redirect. Jobs remain queued;
no worker, VK API, model provider or digest delivery is invoked.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

_conftest_path = Path(__file__).resolve().parents[1] / "conftest.py"
_conftest = next(
    (module for module in tuple(sys.modules.values()) if getattr(module, "__file__", None) == str(_conftest_path)),
    None,
)
if _conftest is None or getattr(_conftest, "TEST_SCHEMA", None) != "test_schema":
    raise RuntimeError("STOP: root conftest/test_schema redirect required; never use --noconftest")
_target = urlsplit(getattr(_conftest, "TEST_DATABASE_URL", ""))
if (
    _target.hostname not in {"localhost", "127.0.0.1"}
    or (_target.port or 5432) != 5432
    or _target.path != "/social_manager"
):
    raise RuntimeError("STOP: only the approved local social_manager/test_schema is allowed")

import pytest
from test_p8_t29_social_launch import Workspace

from app.core.tenant_context import tenant_scope
from app.models import AgentTask, Job
from app.models.managers.agent_task_manager import AgentTaskManager
from app.services.billing.payer import configure_current_personal_account
from app.services.task_launch import launch_social_task
from app.services.task_plans import apply_task_plan

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


@pytest.fixture
async def launch_workspace(request, monkeypatch):
    consent = None
    if request.node.name in {
        "test_composed_same_request_concurrently_admits_one_job",
        "test_report_window_change_conflicts_for_same_request_id",
    }:
        consent = AsyncMock()
        monkeypatch.setattr("app.services.billing.payer.consent_current_user_as_task_payer", consent)
    workspace = await Workspace.create()
    try:
        yield workspace
    finally:
        await workspace.cleanup()
    if consent is not None:
        assert consent.await_count > 0


def _plan(scenario_id: int, source_id: int, *, schedule: bool = False) -> dict:
    trigger = {"type": "schedule", "cron": "0 8 * * *", "timezone": "Europe/Moscow"} if schedule else {"type": "once"}
    return {
        "version": 2,
        "goal": "Find repair requests and summarize evidence",
        "platforms": ["vk"],
        "source_scope": "selected",
        "source_ids": [source_id],
        "collection": {"content_types": ["posts"], "limit": 20, "queries": ["repair"]},
        "window": {"type": "rolling", "days": 3},
        "scenario_id": scenario_id,
        "trigger": trigger,
        "pipeline": {"stages": ["collect", "analyze", "digest"]},
        "digest": {
            "window": {"type": "rolling", "days": 7},
            "group_by": "themes",
            "time_breakdown": False,
        },
    }


async def test_composed_same_request_concurrently_admits_one_job(launch_workspace):
    scenario = await launch_workspace.scenario()
    source = await launch_workspace.source()
    plan = _plan(scenario.id, source.id)
    with launch_workspace.actor_scope():
        await configure_current_personal_account("20")
        first, second = await __import__("asyncio").gather(
            launch_social_task(plan, request_id="p8t40-compose-concurrent-01"),
            launch_social_task(plan, request_id="p8t40-compose-concurrent-01"),
        )

    assert {first["status"], second["status"]} == {"queued", "replayed"}
    assert first["task_id"] == second["task_id"]
    assert first["job_id"] == second["job_id"]
    launch_workspace.task_ids.append(first["task_id"])
    with tenant_scope(launch_workspace.tenant.id):
        task = await AgentTask.objects.get(id=first["task_id"])
        jobs = await Job.objects.filter(agent_task_id=first["task_id"])
    assert task is not None and task.job_type == "analyze"
    assert task.payload["task_plan"]["version"] == 2
    assert task.payload["collect_before_analyze"] is True
    assert len(jobs) == 1 and jobs[0].id == first["job_id"]


async def test_report_window_change_conflicts_for_same_request_id(launch_workspace):
    scenario = await launch_workspace.scenario()
    source = await launch_workspace.source()
    plan = _plan(scenario.id, source.id)
    with launch_workspace.actor_scope():
        await configure_current_personal_account("20")
        first = await launch_social_task(plan, request_id="p8t40-report-conflict-01")
        changed = {**plan, "digest": {**plan["digest"], "window": {"type": "rolling", "days": 8}}}
        with pytest.raises(ValueError, match="different task conditions"):
            await launch_social_task(changed, request_id="p8t40-report-conflict-01")
    launch_workspace.task_ids.append(first["task_id"])
    with tenant_scope(launch_workspace.tenant.id):
        jobs = await Job.objects.filter(agent_task_id=first["task_id"])
    assert len(jobs) == 1


async def test_schedule_materializes_windows_and_pause_edit_invalidate_pending_jobs(launch_workspace):
    scenario = await launch_workspace.scenario()
    source = await launch_workspace.source()
    plan = _plan(scenario.id, source.id, schedule=True)
    due = datetime(2026, 1, 1, 7, tzinfo=timezone.utc)
    fixed_now = datetime(2026, 1, 1, 22, 30, tzinfo=timezone.utc)
    payload = apply_task_plan({}, plan, job_type="analyze")
    with launch_workspace.actor_scope():
        task = await AgentTask.objects.create(
            name="P8-T40 composed schedule",
            cron_expr="0 8 * * *",
            job_type="analyze",
            payload=payload,
            is_active=True,
            next_run_at=due,
            agent_scenario_id=scenario.id,
        )
        launch_workspace.task_ids.append(task.id)
        await AgentTask.objects.set_sources(task.id, [source.id])
        first = await AgentTaskManager()._admit_task_run(
            task.id, now=fixed_now, expected_next_run_at=due, scheduled=True, timezone_name="UTC"
        )
    assert first is not None and first["status"] == "enqueued"
    with tenant_scope(launch_workspace.tenant.id):
        job = await Job.objects.get(id=first["job"].id)
        queued_task = await AgentTask.objects.get(id=task.id)
    assert job is not None and queued_task is not None
    snapshot = job.payload["_effective_snapshot"]["task_payload"]
    materialized = snapshot["task_plan"]
    local_day = fixed_now.astimezone(ZoneInfo("Europe/Moscow")).date()
    assert materialized["window"] == {
        "type": "fixed",
        "start": (local_day - timedelta(days=2)).isoformat(),
        "end": local_day.isoformat(),
    }
    assert materialized["digest"]["window"] == {
        "type": "fixed",
        "start": (local_day - timedelta(days=6)).isoformat(),
        "end": local_day.isoformat(),
    }
    assert materialized["collection"]["date_window"] == {
        "start": (local_day - timedelta(days=2)).isoformat(),
        "end": local_day.isoformat(),
    }

    next_due = queued_task.next_run_at
    assert next_due is not None
    later = next_due + timedelta(minutes=1)
    with launch_workspace.actor_scope():
        overlap = await AgentTaskManager()._admit_task_run(
            task.id, now=later, expected_next_run_at=next_due, scheduled=True, timezone_name="UTC"
        )
    assert overlap is not None and overlap["status"] == "busy"
    with tenant_scope(launch_workspace.tenant.id):
        assert len(await Job.objects.filter(agent_task_id=task.id)) == 1

    # Pausing removes the replaceable queued occurrence. Resuming admits one new
    # due occurrence; editing its canonical definition removes that stale job.
    with launch_workspace.actor_scope():
        await AgentTask.objects.update_by_id(task.id, is_active=False)
    with tenant_scope(launch_workspace.tenant.id):
        assert await Job.objects.filter(agent_task_id=task.id) == []
    with launch_workspace.actor_scope():
        await AgentTask.objects.update_by_id(task.id, is_active=True)
        resumed = await AgentTask.objects.get(id=task.id)
        assert resumed is not None
        resumed_due = resumed.next_run_at
        assert resumed_due is not None
        second = await AgentTaskManager()._admit_task_run(
            task.id,
            now=resumed_due + timedelta(minutes=1),
            expected_next_run_at=resumed_due,
            scheduled=True,
            timezone_name="UTC",
        )
        assert second is not None and second["status"] == "enqueued"
        edited = apply_task_plan(
            {},
            {**plan, "digest": {**plan["digest"], "window": {"type": "rolling", "days": 8}}},
            job_type="analyze",
        )
        await AgentTask.objects.update_by_id(task.id, payload=edited)
    with tenant_scope(launch_workspace.tenant.id):
        assert await Job.objects.filter(agent_task_id=task.id) == []
