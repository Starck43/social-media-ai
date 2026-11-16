"""P8-T39 DB acceptance for chat one-off launch and result handoff.

Requires root conftest's approved test_schema redirect. No collection worker or
external provider runs; jobs are synthetic queue rows cleaned with their task.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

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
from app.models import AgentTask, Job, Source
from app.models.managers.agent_task_manager import AgentTaskManager
from app.models.managers.job_manager import JobManager
from app.services.billing.payer import configure_current_personal_account

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


@pytest.fixture
async def launch_workspace():
    workspace = await Workspace.create()
    try:
        yield workspace
    finally:
        await workspace.cleanup()


def _discovery_collect_plan() -> dict:
    return {
        "goal": "Find recent public updates",
        "platforms": ["vk"],
        "source_scope": "discovery",
        "collection": {
            "limit": 10,
            "discovery": {"communities": True, "queries": ["public updates"]},
        },
        "window": {"type": "rolling", "days": 1},
        "trigger": {"type": "once"},
    }


async def test_collect_discovery_launch_replays_and_projects_partial_results(launch_workspace, monkeypatch):
    """Registered chat tools preview, launch, retrieve and follow up one discovery plan."""
    from app.agent.tools import call_tool

    monkeypatch.setattr(
        "app.services.collection.entitlement_policy.preview_entitlements", AsyncMock(return_value={"allowed": True})
    )
    monkeypatch.setattr("app.services.social.vk_task_access.preview_vk_access", AsyncMock(return_value={"mode": "api"}))
    consent = AsyncMock()
    monkeypatch.setattr("app.services.billing.payer.consent_current_user_as_task_payer", consent)
    plan = _discovery_collect_plan()
    with launch_workspace.actor_scope():
        await configure_current_personal_account("20")
        before_sources = await Source.objects.count()
        preview = await call_tool("task_plan", {"plan": plan})
        first = await call_tool(
            "task_execute_once",
            {"plan": plan, "request_id": "p8t39-collect-discovery-01", "job_type": "collect"},
        )
        launch_workspace.task_ids.append(first["task_id"])
        replay = await call_tool(
            "task_execute_once",
            {"plan": plan, "request_id": "p8t39-collect-discovery-01", "job_type": "collect"},
        )

    assert preview["status"] == "validated_preview" and preview["writes"] is False
    assert first["status"] == "queued"
    assert replay["status"] == "replayed"
    assert replay["task_id"] == first["task_id"]
    assert replay["job_id"] == first["job_id"]
    assert consent.await_count == 1
    with tenant_scope(launch_workspace.tenant.id):
        task = await AgentTask.objects.prefetch_related("sources").get(id=first["task_id"])
        jobs = await Job.objects.filter(agent_task_id=first["task_id"])
        after_launch_sources = await Source.objects.count()
    assert task is not None
    assert task.job_type == "collect"
    assert task.payload["task_plan"]["source_scope"] == "discovery"
    assert len(jobs) == 1 and jobs[0].id == first["job_id"]
    assert after_launch_sources == before_sources, "launch admission must not create shared Source rows"
    with launch_workspace.actor_scope():
        assert await AgentTaskManager().get_sources(task.id) == []
        saved = await call_tool("task_get", {"task_id": task.id})
        revised_plan = {**saved["plan"], "goal": "Find recent repair requests"}
        revised_preview = await call_tool("task_plan", {"plan": revised_plan})
        saved_after_preview = await call_tool("task_get", {"task_id": task.id})
    assert saved["plan_status"] == "available"
    assert saved["plan"]["goal"] == plan["goal"]
    assert revised_preview["plan"]["goal"] == "Find recent repair requests"
    assert saved_after_preview["plan"]["goal"] == plan["goal"], "preview follow-up must not persist an edit"

    # Exercise the actual discovery writer with the provider boundary mocked.
    from app.services.collection.intent import parse_collection_intent
    from app.services.collection.population import resolve_population

    shared = await launch_workspace.source()
    claimed_job = await JobManager.claim_job(first["job_id"])
    assert claimed_job is not None and claimed_job.status == "running"
    with launch_workspace.actor_scope():
        from app.services.social import factory

        client = SimpleNamespace(search_groups=AsyncMock(return_value=[{"id": 918273, "name": "P8-T39 lane"}]))
        monkeypatch.setattr(factory, "get_social_client", lambda _platform: client)
        population = await resolve_population(
            task=task,
            intent=parse_collection_intent({"collection": task.payload["collection"]}),
            platform="vk",
            dry_run=False,
            job_id=first["job_id"],
            owner_user_id=launch_workspace.user.id,
        )
        lane = await Source.objects.get(id=population.created_source_ids[0])
        launch_workspace.source_ids.extend(population.created_source_ids)
        assert lane.owner_task_id == task.id
        assert lane.is_active is True
        assert lane.id != shared.id
        assert await Source.objects.count() == before_sources + 2

        # A separate all-scope task gets only its admission snapshot; a
        # discovered lane owned by the first task cannot leak into it.
        from app.jobs.handlers import _resolve_sources

        another_task = SimpleNamespace(
            id=task.id + 1,
            tenant_id=launch_workspace.tenant.id,
            payload={"task_plan": {"source_scope": "all", "platforms": ["vk"]}},
            sources=[],
        )
        isolated = await _resolve_sources(another_task, payload={"source_ids": [shared.id]})
    assert [source.id for source in isolated] == [shared.id]
    assert lane.id not in {source.id for source in isolated}

    with tenant_scope(launch_workspace.tenant.id):
        await Job.objects.update_by_id(
            first["job_id"],
            status="done",
            result={"sources": 2, "items": 7, "error": 1, "provider_body": "must stay private"},
        )
    with launch_workspace.actor_scope():
        result = await call_tool("task_result", {"task_id": first["task_id"], "job_id": first["job_id"]})

    assert result["job_status"] == "done"
    assert result["counts"] == {"sources": 2, "items": 7, "error": 1}
    assert result["analyses"] == []
    assert "provider_body" not in result
    assert "must stay private" not in repr(result)


async def test_collect_launch_rolls_back_task_when_source_binding_write_fails(launch_workspace, monkeypatch):
    """A failure after task insertion cannot leave an unbound runnable draft."""
    original = AgentTaskManager.set_sources

    async def fail_after_task_insert(self, task_id, source_ids, session=None):
        await original(self, task_id, source_ids, session=session)
        raise RuntimeError("injected P8-T39 source binding failure")

    monkeypatch.setattr(AgentTaskManager, "set_sources", fail_after_task_insert)
    monkeypatch.setattr("app.services.billing.payer.consent_current_user_as_task_payer", AsyncMock())
    with launch_workspace.actor_scope():
        await configure_current_personal_account("20")
        with pytest.raises(RuntimeError, match="injected P8-T39 source binding failure"):
            from app.services.task_launch import launch_social_task

            await launch_social_task(
                _discovery_collect_plan(), request_id="p8t39-collect-rollback-01", job_type="collect"
            )

    with tenant_scope(launch_workspace.tenant.id):
        assert await AgentTask.objects.filter(tenant_id=launch_workspace.tenant.id) == []
        assert await Job.objects.filter(tenant_id=launch_workspace.tenant.id) == []
