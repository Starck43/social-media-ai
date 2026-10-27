"""Pure contract checks for versioned composed social task plans."""

from datetime import datetime, timezone

import pytest

from app.services.task_plans import (
    TaskPlanError,
    apply_task_plan,
    materialize_task_plan_window,
    parse_task_plan,
    prepare_task_plan_update,
    task_plan_block,
)


def _composed(*, stages=("collect", "analyze", "digest"), collection_days=3, digest_days=7):
    return {
        "version": 2,
        "platforms": ["vk"],
        "source_scope": "all",
        "source_ids": [],
        "collection": {},
        "window": {"type": "rolling", "days": collection_days},
        "scenario_id": 9,
        "trigger": {"type": "schedule", "cron": "0 8 * * *", "timezone": "Europe/Moscow"},
        "pipeline": {"stages": list(stages)},
        "digest": {
            "window": {"type": "rolling", "days": digest_days},
            "group_by": "topic_chains",
            "time_breakdown": True,
        },
    }


def test_v1_plan_roundtrip_remains_v1_and_unchanged():
    raw = {
        "version": 1,
        "platforms": ["vk"],
        "source_scope": "all",
        "source_ids": [],
        "collection": {},
        "window": {"type": "rolling", "days": 2},
        "scenario_id": 9,
        "trigger": {"type": "once"},
    }
    canonical = task_plan_block(parse_task_plan(raw))
    assert canonical["version"] == 1
    assert task_plan_block(parse_task_plan(canonical)) == canonical


@pytest.mark.parametrize("stages", [("collect", "analyze", "digest"), ("analyze", "digest")])
def test_v2_pipeline_roundtrip_and_projection(stages):
    raw = _composed(stages=stages)
    parsed = parse_task_plan(raw)
    canonical = task_plan_block(parsed)
    assert canonical["pipeline"] == raw["pipeline"]
    assert canonical["digest"] == raw["digest"]
    assert task_plan_block(parse_task_plan(canonical)) == canonical
    projected = apply_task_plan({}, raw, job_type="analyze")
    assert projected["collect_before_analyze"] is ("collect" in stages)


def test_v2_requires_analyze_task_type_and_clear_removes_pipeline_projection():
    with pytest.raises(TaskPlanError):
        apply_task_plan({}, _composed(), job_type="collect")
    payload = apply_task_plan({}, _composed(), job_type="analyze")
    cleared = apply_task_plan(payload, None, job_type="analyze")
    assert "collect_before_analyze" not in cleared
    assert "task_plan" not in cleared


def test_ordinary_update_preserves_composed_plan_and_projection():
    payload = apply_task_plan({"other_setting": 1}, _composed(), job_type="analyze")
    update = prepare_task_plan_update(payload, {"name": "Renamed"}, job_type="analyze")
    assert update.changes["payload"]["task_plan"] == payload["task_plan"]
    assert update.changes["payload"]["collect_before_analyze"] is True


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["pipeline"].update(send=True),
        lambda p: p["digest"].update(delivery={"target": "x"}),
        lambda p: p["pipeline"].update(stages=["collect", "digest"]),
        lambda p: p["digest"].update(group_by="days"),
        lambda p: p["digest"].update(time_breakdown=1),
    ],
)
def test_v2_rejects_unsupported_pipeline_or_digest_fields_and_values(mutation):
    raw = _composed()
    mutation(raw)
    with pytest.raises(TaskPlanError):
        parse_task_plan(raw)


def test_digest_window_materializes_in_workspace_zone_without_changing_collection_bounds():
    raw = _composed(collection_days=2, digest_days=5)
    payload = apply_task_plan({}, raw, job_type="analyze")
    materialized = materialize_task_plan_window(payload, datetime(2026, 1, 1, 22, 30, tzinfo=timezone.utc))
    plan = materialized["task_plan"]
    assert plan["window"] == {"type": "fixed", "start": "2026-01-01", "end": "2026-01-02"}
    assert plan["digest"]["window"] == {"type": "fixed", "start": "2025-12-29", "end": "2026-01-02"}
    assert plan["collection"]["date_window"] == {"start": "2026-01-01", "end": "2026-01-02"}
    assert materialized["cli_dates"] == {"start_date": "2026-01-01", "end_date": "2026-01-02"}


def test_fixed_digest_reporting_window_is_preserved():
    raw = _composed()
    raw["digest"]["window"] = {"type": "fixed", "start": "2025-12-01", "end": "2025-12-10"}
    payload = apply_task_plan({}, raw, job_type="analyze")
    materialized = materialize_task_plan_window(payload, datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert materialized["task_plan"]["digest"]["window"] == raw["digest"]["window"]
