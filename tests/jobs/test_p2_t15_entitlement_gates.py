"""P2-T15 execution gates run before provider/model work and fail terminally."""

from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.tenant_context import tenant_scope
from app.jobs import dispatcher, handlers
from app.jobs.claim_outcomes import JobClaim, JobOutcomeReceipt, OutcomeAck
from app.services import task_pipeline
from app.services.tenancy.entitlements import EntitlementDecision, EntitlementDenied

__source_only__ = True

CAPABILITY = "connector.vk.read"


def _denial(reason="plan_expired"):
    return EntitlementDenied(
        EntitlementDecision(
            allowed=False,
            plan="pro",
            catalog_version=1,
            required=(CAPABILITY,),
            denials=({"capability": CAPABILITY, "reason": reason},),
        )
    )


def _manager(monkeypatch, *, side_effect):
    manager = NS(admit_execution=AsyncMock(side_effect=side_effect))
    monkeypatch.setattr("app.models.managers.entitlement_manager.EntitlementManager", lambda: manager)
    return manager


async def test_collection_denial_stops_before_discovery_or_provider_calls(monkeypatch):
    denied = _denial()
    manager = _manager(monkeypatch, side_effect=denied)
    discovery = AsyncMock(side_effect=AssertionError("discovery ran after entitlement denial"))
    mentions = AsyncMock(side_effect=AssertionError("mention search ran after entitlement denial"))
    collector = Mock(side_effect=AssertionError("provider collector was constructed after entitlement denial"))
    monkeypatch.setattr(handlers, "_resolve_sources", AsyncMock(return_value=[]))
    monkeypatch.setattr(handlers, "_run_population_step", discovery)
    monkeypatch.setattr(handlers, "_run_mention_lanes_step", mentions)
    monkeypatch.setattr("app.services.monitoring.collector.ContentCollector", collector)

    with pytest.raises(EntitlementDenied) as raised:
        await handlers.handle_collect({"job_id": 17}, _resolved_context=(None, None, {}))

    assert raised.value is denied
    manager.admit_execution.assert_awaited_once_with(set(), job_id=17)
    discovery.assert_not_awaited()
    mentions.assert_not_awaited()
    collector.assert_not_called()


async def test_collection_rechecks_after_discovery_before_constructing_provider(monkeypatch):
    denied = _denial("not_granted")
    manager = _manager(monkeypatch, side_effect=[None, denied])
    collector = Mock(side_effect=AssertionError("provider collector was constructed after entitlement denial"))
    task = NS(id=9, tenant_id=3, payload={}, sources=[], dry_run=False)
    source = NS(id=11, tenant_id=3, platform=NS(platform_type=NS(db_value="vk")), source_type="channel")
    monkeypatch.setattr(handlers, "_resolve_sources", AsyncMock(side_effect=[[], [source]]))
    monkeypatch.setattr(handlers, "_run_population_step", AsyncMock(return_value={"created": 1, "source_ids": [11]}))
    monkeypatch.setattr(handlers, "_run_mention_lanes_step", AsyncMock(return_value=None))
    monkeypatch.setattr(handlers, "_load_task", AsyncMock(return_value=task))
    monkeypatch.setattr("app.services.monitoring.collector.ContentCollector", collector)

    with pytest.raises(EntitlementDenied) as raised:
        await handlers.handle_collect({"job_id": 18}, _resolved_context=(None, task, {}))

    assert raised.value is denied
    assert manager.admit_execution.await_count == 2
    assert manager.admit_execution.await_args_list[0].kwargs == {"job_id": 18}
    assert manager.admit_execution.await_args_list[1].kwargs == {"job_id": 18}
    assert manager.admit_execution.await_args_list[1].args[0] == {CAPABILITY}
    collector.assert_not_called()


async def test_staged_only_analysis_denial_stops_before_model_work(monkeypatch):
    denied = _denial()
    manager = _manager(monkeypatch, side_effect=denied)
    analyzer = Mock(side_effect=AssertionError("model analyzer was constructed after entitlement denial"))
    task = NS(id=9, tenant_id=3, payload={}, dry_run=False, agent_scenario=None)
    source = NS(id=11, tenant_id=3, platform=NS(platform_type=NS(db_value="vk")), source_type="channel")
    monkeypatch.setattr(handlers, "_resolve_sources", AsyncMock(return_value=[source]))
    monkeypatch.setattr("app.services.ai.analyzer.AIAnalyzer", analyzer)

    with pytest.raises(EntitlementDenied) as raised:
        await handlers.handle_analyze({"job_id": 19}, _resolved_context=(None, task, {}))

    assert raised.value is denied
    manager.admit_execution.assert_awaited_once_with({CAPABILITY}, job_id=19)
    analyzer.assert_not_called()


def _pipeline_context(stages):
    plan = {
        "version": 2,
        "platforms": ["vk"],
        "source_scope": "selected",
        "source_ids": [11],
        "scenario_id": 5,
        "pipeline": {"stages": stages},
    }
    task_payload = {"task_plan": plan, "source_ids": [11]}
    snapshot = {
        "version": 1,
        "task_id": 7,
        "task_payload": task_payload,
        "scenario_id": 5,
        "trigger_type": None,
        "trigger_config": None,
        "action_type": None,
        "rate_limit_per_hour": None,
        "cooldown_seconds": None,
        "dry_run": False,
    }
    task = NS(
        id=7,
        tenant_id=3,
        payload=task_payload,
        sources=[],
        cron_expr="@once",
        launch_job_id=None,
        is_active=True,
    )
    payload = {"agent_task_id": 7, "job_id": 23, "attempts": 1, "_effective_snapshot": snapshot}
    return task, payload


async def test_pipeline_collect_gate_runs_before_collection_stage(monkeypatch):
    denied = _denial()
    task, payload = _pipeline_context(["collect", "analyze", "digest"])
    collect = AsyncMock(side_effect=AssertionError("collection stage ran after entitlement denial"))
    monkeypatch.setattr(handlers, "_load_task", AsyncMock(return_value=task))
    monkeypatch.setattr(handlers, "_resolve_sources", AsyncMock(return_value=[NS(id=11)]))
    monkeypatch.setattr(handlers, "handle_collect", collect)
    monkeypatch.setattr(task_pipeline, "_authorized_task", AsyncMock())
    monkeypatch.setattr(task_pipeline, "_admit_entitlements", AsyncMock(side_effect=denied))

    with tenant_scope(3), pytest.raises(EntitlementDenied):
        await task_pipeline.execute_task_pipeline(payload)

    task_pipeline._admit_entitlements.assert_awaited_once()
    collect.assert_not_awaited()


async def test_pipeline_rechecks_before_digest_generation(monkeypatch):
    denied = _denial("not_granted")
    task, payload = _pipeline_context(["analyze", "digest"])
    source = NS(id=11)
    analyze = AsyncMock(
        return_value={
            "sources": 1,
            "analyzed": 0,
            "skipped": 1,
            "error": 0,
            "staged_errors": 0,
            "per_source": [],
        }
    )
    generate = AsyncMock(side_effect=AssertionError("digest generated after entitlement denial"))
    monkeypatch.setattr(handlers, "_load_task", AsyncMock(return_value=task))
    monkeypatch.setattr(handlers, "_resolve_sources", AsyncMock(return_value=[source]))
    monkeypatch.setattr(handlers, "handle_analyze", analyze)
    monkeypatch.setattr(task_pipeline, "_authorized_task", AsyncMock())
    monkeypatch.setattr(task_pipeline, "_require_active_scenario", AsyncMock())
    monkeypatch.setattr(task_pipeline, "_admit_entitlements", AsyncMock(side_effect=[None, denied]))
    monkeypatch.setattr(task_pipeline, "_generate_digest", generate)
    monkeypatch.setattr("app.jobs.progress.pipeline_checkpoint", AsyncMock())

    with tenant_scope(3), pytest.raises(EntitlementDenied):
        await task_pipeline.execute_task_pipeline(payload)

    assert task_pipeline._admit_entitlements.await_count == 2
    analyze.assert_awaited_once()
    generate.assert_not_awaited()


async def test_dispatcher_persists_policy_denial_as_confirmed_terminal_failure(monkeypatch):
    denied = _denial()
    job = NS(
        id=31,
        tenant_id=3,
        agent_task_id=7,
        initiated_by_user_id=5,
        job_type="collect",
        payload={},
        result=None,
        status="running",
        error=None,
        attempts=1,
        started_at=datetime(2026, 11, 12, tzinfo=timezone.utc),
    )
    claim = JobClaim.capture(job)
    receipt = JobOutcomeReceipt(claim, OutcomeAck.FAILED, error=str(denied))
    jobs = NS(renew_claim=AsyncMock(return_value=True), mark_failed=AsyncMock(return_value=receipt))
    monkeypatch.setattr(dispatcher, "jobs", jobs)
    monkeypatch.setattr(dispatcher, "_run_ordinary_handler", AsyncMock(side_effect=denied))
    monkeypatch.setattr(dispatcher, "_post_ordinary_outcome", AsyncMock(return_value=receipt))
    monkeypatch.setattr(dispatcher, "_log_job", Mock())

    result = await dispatcher._execute_ordinary(job, AsyncMock(), {}, allow_retry=True)

    jobs.mark_failed.assert_awaited_once_with(31, error=str(denied), allow_retry=False, claim=claim)
    assert result is receipt
