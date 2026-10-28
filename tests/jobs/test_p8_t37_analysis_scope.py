"""Actual worker handoff/resolver with local ORM/provider doubles only."""

import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from tests.shared.task_plan_support import ROOT, Query, load_plans, plan
from tests.shared.decomposition_source_loader import load_decomposed_source as load_isolated_source

__source_only__ = True


def worker(rows, collected, *, scope="discovery", admitted=(), planned=True):
    plans, _ = load_plans()
    raw = plan(source_scope=scope, source_ids=list(admitted) if scope == "selected" else [])
    payload = plans.apply_task_plan({}, raw, job_type="analyze")
    if not planned:
        payload.pop("task_plan")
    payload["collect_before_analyze"] = True
    scenario = NS(id=3, is_active=True)
    # Include unrelated current links to prove this cannot widen the job.
    task = NS(
        id=7,
        tenant_id=2,
        sources=list(rows),
        payload=payload,
        agent_scenario=scenario,
        trigger_config=None,
        action_type=None,
        dry_run=False,
    )
    queries = []

    def source_filter(**filters):
        queries.append(filters)
        result = [row for row in rows if row.is_active and row.tenant_id == 2]
        result = [row for row in result if row.id in filters.get("id__in", [r.id for r in result])]
        return Query(result)

    def staged(source_id, **kwargs):
        return [
            NS(
                selection_status="selected",
                as_agent_item=lambda: {"content_hash": f"hash-{source_id}", "text": "request"},
            )
        ]

    analyzer = NS(
        analyze_content=AsyncMock(return_value=[]), completed_hashes=set(), current_fingerprint=None, reported_errors=0
    )
    selection = load_isolated_source("_scope_selection", ROOT / "app/services/monitoring/selection.py")
    activity = load_isolated_source("_scope_activity", ROOT / "app/jobs/source_activity.py")
    progress = NS(
        begin_phase=AsyncMock(),
        step=AsyncMock(),
        update_current=AsyncMock(),
        ProgressStopped=type("ProgressStopped", (Exception,), {}),
    )
    imports = {
        "app.jobs.source_activity": activity,
        "app.services.social.credentials": NS(AuthorizationRequired=type("AuthorizationRequired", (Exception,), {})),
        "app.services.monitoring": NS(selection=selection),
        "app.models": NS(
            Source=NS(objects=NS(filter=source_filter)),
            AgentScenario=NS(),
            AIAnalytics=NS(),
            BotAction=NS(),
            CollectedItem=NS(
                objects=NS(count_for_source=AsyncMock(return_value=1), for_source=AsyncMock(side_effect=staged))
            ),
        ),
        "app.models.managers.agent_task_manager": NS(AgentTaskManager=lambda: NS()),
        "app.services.ai.trigger_evaluator": NS(trigger_evaluator=NS()),
        "app.services.social.guards": NS(guards_checker=NS()),
        "app.types": NS(BotActionStatus=NS(PENDING="pending")),
        "app.jobs": NS(progress=progress),
        "app.jobs.progress": progress,
        "app.services.ai.analyzer": NS(AIAnalyzer=lambda: analyzer),
        "app.services.ai.dedup": NS(analysed_hashes=lambda value: set()),
    }
    module = load_isolated_source("_handoff_handlers", ROOT / "app/jobs/handlers.py", imports)
    module._load_task = AsyncMock(return_value=task)
    module._retire_staged = AsyncMock(return_value=0)
    module._count_failed_staged = AsyncMock(return_value=0)
    module.handle_collect = AsyncMock(
        return_value={
            "error": 0,
            "per_source": [{"source_id": i, "outcome": "collected", "items": 1} for i in collected],
        }
    )
    job = {
        **payload,
        "agent_task_id": 7,
        "source_ids": list(admitted),
        "job_id": 9,
        "discovered_source_ids": [404],
    }  # client-like key must not authorize anything
    result = asyncio.run(module.handle_analyze(job))
    return result, analyzer, module, queries


def source(source_id, *, tenant=2, active=True, platform="vk"):
    return NS(
        id=source_id,
        tenant_id=tenant,
        is_active=active,
        name=f"source-{source_id}",
        platform=NS(platform_type=platform),
        params={},
    )


def test_new_discovery_source_reaches_the_actual_analyzer():
    result, analyzer, module, _ = worker([source(99)], [99])
    assert result["sources"] == 1 and result["error"] == 0
    analyzer.analyze_content.assert_awaited_once()
    assert analyzer.analyze_content.await_args.args[1].id == 99
    module.handle_collect.assert_awaited_once()
    assert result["per_source"][0]["selection"]["eligible"] == 1


def test_collected_scope_cannot_widen_to_mutable_links_or_uncollected_admission():
    result, analyzer, _, _ = worker([source(11), source(99), source(404)], [99], scope="selected", admitted=[11])
    assert result["sources"] == 1
    assert [call.args[1].id for call in analyzer.analyze_content.await_args_list] == [99]


@pytest.mark.parametrize("collected", [[], [99]])
def test_empty_collection_does_not_fall_back_to_all_or_admitted_sources(collected):
    result, analyzer, _, _ = worker([source(11)], collected, scope="all", admitted=[11])
    assert result["sources"] == 0
    analyzer.analyze_content.assert_not_awaited()


@pytest.mark.parametrize("changed", [source(99, active=False), source(99, tenant=3), source(99, platform="telegram")])
def test_analysis_rechecks_active_tenant_and_platform_after_collection(changed):
    result, analyzer, _, queries = worker([changed], [99])
    assert queries and result["sources"] == 0
    analyzer.analyze_content.assert_not_awaited()


def test_legacy_nonplanned_unified_run_keeps_existing_source_resolution():
    result, analyzer, _, _ = worker([source(11)], [], admitted=[11], planned=False)
    assert result["sources"] == 1
    analyzer.analyze_content.assert_awaited_once()
    assert analyzer.analyze_content.await_args.args[1].id == 11
