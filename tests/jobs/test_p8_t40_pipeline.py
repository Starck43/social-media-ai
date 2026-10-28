from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.tenant_context import tenant_scope
from app.services import task_pipeline


@pytest.fixture
def task_snapshot():
    task_payload = {
        "source_ids": [11],
        "scenario_id": 42,
        "collect_before_analyze": True,
        "task_plan": {
            "version": 2,
            "source_scope": "selected",
            "source_ids": [11],
            "scenario_id": 42,
            "pipeline": {"stages": ["collect", "analyze", "digest"]},
            "digest": {
                "window": {"type": "fixed", "start": "2026-10-20", "end": "2026-10-27"},
                "group_by": "themes",
                "time_breakdown": False,
            },
        },
    }
    return {
        "version": 1,
        "task_id": 7,
        "task_payload": task_payload,
        "trigger_type": "OTHER",
        "trigger_config": {"snapshot": True},
        "action_type": "ALERT",
        "rate_limit_per_hour": 2,
        "cooldown_seconds": 30,
        "dry_run": False,
    }


def _job_payload(snapshot):
    return {
        "agent_task_id": 7,
        "job_id": 99,
        "attempts": 1,
        "_effective_snapshot": snapshot,
        "_trusted_initiated_by_user_id": 5,
        "_prior_result": {},
    }


@pytest.mark.parametrize(
    "pipeline",
    [
        {"version": 2, "job_id": 99, "task_id": 7, "attempt": 1, "stages": {"collect": {"state": "completed"}}},
        {
            "version": 1,
            "job_id": 99,
            "task_id": 7,
            "attempt": 1,
            "stages": {"collect": {"state": "completed", "attempt": 1}},
        },
        {
            "version": 1,
            "job_id": 99,
            "task_id": 7,
            "attempt": 1,
            "stages": {"future": {"state": "started", "attempt": 1}},
        },
        {
            "version": 1,
            "job_id": 99,
            "task_id": 7,
            "attempt": 1,
            "stages": {"collect": {"state": "unknown", "attempt": 1}},
        },
    ],
)
def test_malformed_persisted_checkpoint_fails_closed(pipeline):
    with pytest.raises(task_pipeline.PipelineReviewRequired, match="pipeline_checkpoint_invalid_requires_review"):
        task_pipeline._valid_checkpoint(pipeline, job_id=99, task_id=7)


@pytest.mark.asyncio
async def test_composed_pipeline_uses_admitted_scope_and_finishes_each_stage(monkeypatch, task_snapshot):
    from app.jobs import handlers, progress

    task = SimpleNamespace(
        id=7,
        tenant_id=3,
        payload={"task_plan": {"source_scope": "all", "scenario_id": 999}, "source_ids": [88]},
        sources=[SimpleNamespace(id=88)],
        action_type="CHANGED",
        rate_limit_per_hour=99,
        cooldown_seconds=999,
        is_active=True,
    )
    source = SimpleNamespace(id=11)
    order = []
    checkpoints = []
    monkeypatch.setattr(handlers, "_load_task", AsyncMock(return_value=task))

    async def resolve_sources(task_view, payload, discovered_source_ids=None):
        assert task_view.payload == task_snapshot["task_payload"]
        assert payload["source_ids"] == [11]
        assert task_view.trigger_config == {"snapshot": True}
        return [source]

    async def collect(payload, *, stage_only, _resolved_context):
        order.append("collect")
        assert stage_only is True
        assert _resolved_context[2] == task_snapshot["task_payload"]
        return {
            "sources": 1,
            "items": 1,
            "new_items": 1,
            "error": 0,
            "rate_limited": 0,
            "per_source": [
                {
                    "source_id": 11,
                    "selected": 1,
                    "content_hashes": ["a" * 64],
                    "outcome": "collected",
                }
            ],
        }

    async def analyze(payload, *, _resolved_context, collection_stats_override):
        order.append("analyze")
        assert collection_stats_override["per_source"][0]["content_hashes"] == ["a" * 64]
        assert _resolved_context[1].id == 7
        return {
            "sources": 1,
            "analyzed": 1,
            "skipped": 0,
            "error": 0,
            "staged_errors": 0,
            "per_source": [{"source_id": 11, "analyzed": 1, "analytics_ids": [123]}],
        }

    async def generate(task_view, plan, payload, source_ids, *, collection, analysis):
        order.append("digest")
        assert source_ids == [11]
        assert task_view.payload == task_snapshot["task_payload"]
        return {"status": "complete", "generated": True, "delivered": 0, "text": "report"}

    async def allow(*args, **kwargs):
        return None

    async def write(checkpoint):
        checkpoints.append(deepcopy(checkpoint))

    monkeypatch.setattr(handlers, "_resolve_sources", resolve_sources)
    monkeypatch.setattr(handlers, "handle_collect", collect)
    monkeypatch.setattr(handlers, "handle_analyze", analyze)
    monkeypatch.setattr(task_pipeline, "_authorized_task", allow)
    monkeypatch.setattr(task_pipeline, "_require_active_scenario", allow)
    monkeypatch.setattr(task_pipeline, "_admit_entitlements", allow)
    monkeypatch.setattr(task_pipeline, "_generate_digest", generate)
    monkeypatch.setattr(progress, "pipeline_checkpoint", write)

    with tenant_scope(3):
        result = await task_pipeline.execute_task_pipeline(_job_payload(task_snapshot))

    assert order == ["collect", "analyze", "digest"]
    assert [checkpoints[-1]["stages"][name]["state"] for name in ("collect", "analyze", "digest")] == [
        "completed",
        "completed",
        "completed",
    ]
    assert result["collected"] == 1
    assert result["analyzed"] == 1
    assert result["per_source"][0]["analytics_ids"] == [123]
    assert result["digest"]["delivered"] == 0
    assert result["digest"]["text"] == "report"


@pytest.mark.asyncio
async def test_previous_started_stage_requires_review_without_repeating_work(monkeypatch):
    from app.jobs import progress

    checkpoint = {
        "version": 1,
        "job_id": 99,
        "task_id": 7,
        "attempt": 1,
        "stages": {"digest": {"state": "started", "attempt": 1}},
    }
    writes = []
    operation = AsyncMock()
    monkeypatch.setattr(
        progress, "pipeline_checkpoint", AsyncMock(side_effect=lambda value: writes.append(deepcopy(value)))
    )

    with pytest.raises(task_pipeline.PipelineReviewRequired, match="pipeline_stage_outcome_requires_review"):
        await task_pipeline._stage(checkpoint, "digest", operation, attempt=2)

    operation.assert_not_awaited()
    assert writes[-1]["stages"]["digest"]["state"] == "review_required"


@pytest.mark.asyncio
async def test_payer_revocation_at_digest_boundary_prevents_report_generation(monkeypatch, task_snapshot):
    from app.jobs import handlers, progress

    task = SimpleNamespace(id=7, tenant_id=3, payload={}, sources=[], is_active=True)
    source = SimpleNamespace(id=11)
    generated = AsyncMock()
    monkeypatch.setattr(handlers, "_load_task", AsyncMock(return_value=task))
    monkeypatch.setattr(handlers, "_resolve_sources", AsyncMock(return_value=[source]))
    monkeypatch.setattr(handlers, "handle_collect", AsyncMock(return_value={"per_source": []}))
    monkeypatch.setattr(handlers, "handle_analyze", AsyncMock(return_value={"per_source": []}))
    monkeypatch.setattr(task_pipeline, "_admit_entitlements", AsyncMock())
    monkeypatch.setattr(task_pipeline, "_require_active_scenario", AsyncMock())

    async def authorize(_task, _payload, *, stage):
        if stage == "digest":
            raise PermissionError("pipeline_payer_changed")

    monkeypatch.setattr(task_pipeline, "_authorized_task", authorize)
    monkeypatch.setattr(task_pipeline, "_generate_digest", generated)
    monkeypatch.setattr(progress, "pipeline_checkpoint", AsyncMock())

    with tenant_scope(3), pytest.raises(PermissionError, match="pipeline_payer_changed"):
        await task_pipeline.execute_task_pipeline(_job_payload(task_snapshot))

    generated.assert_not_awaited()


@pytest.mark.asyncio
async def test_generated_report_includes_current_partial_coverage(monkeypatch):
    from app.services.digest import builder

    observed = {}

    async def aggregate(period, source_ids, group_by, time_breakdown, scenario_id, *, report_scope):
        observed["period"] = period
        observed["scope"] = report_scope
        return (
            {"coverage_note": "Historical coverage note", "stats": {"analyses": 1}},
            report_scope.start,
            report_scope.end,
        )

    async def summarize(data):
        observed["coverage_note"] = data["coverage_note"]
        return None, {"model": None, "cost": None}

    def render(data, *, summary):
        return data["coverage_note"]

    monkeypatch.setattr(builder, "aggregate", aggregate)
    monkeypatch.setattr(builder, "_summarize", summarize)
    monkeypatch.setattr(builder, "render_digest", render)
    plan = {
        "version": 2,
        "scenario_id": 42,
        "digest": {
            "window": {"type": "fixed", "start": "2026-10-20", "end": "2026-10-27"},
            "group_by": "themes",
            "time_breakdown": False,
        },
    }
    collection = {
        "status": "partial",
        "counts": {"error": 1, "rate_limited": 1},
        "per_source": [
            {"collection": {"warnings": ["comments_unsupported"]}, "selection": {"rejected": 2, "needs_review": 1}}
        ],
    }
    analysis = {"status": "partial", "counts": {"error": 1, "staged_errors": 2}}

    with tenant_scope(3):
        result = await task_pipeline._generate_digest(
            SimpleNamespace(id=7),
            plan,
            {"task_plan": plan},
            [11],
            collection=collection,
            analysis=analysis,
        )

    assert observed["period"] == "custom"
    assert observed["scope"].source_ids == frozenset({11})
    assert observed["scope"].scenario_id == 42
    assert "Historical coverage note" in observed["coverage_note"]
    assert "comments_unsupported" in result["text"]
    assert "Analysis was partial: 1 source errors and 2 staged-item errors." in result["text"]
    assert result["coverage_status"] == "partial"
    assert result["delivered"] == 0
