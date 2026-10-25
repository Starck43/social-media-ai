"""Shared social task-plan validation; pure tests with no database/provider calls."""

from datetime import date, datetime, timezone

import pytest

from app.services.task_plans import (
    TaskPlanError,
    apply_task_plan,
    materialize_task_plan_window,
    parse_task_plan,
    task_plan_block,
)


def _plan(**overrides):
    value = {
        "version": 1,
        "goal": "Find recent renovation requests",
        "platforms": ["vk"],
        "source_scope": "discovery",
        "collection": {
            "limit": 100,
            "discovery": {"communities": True, "queries": ["renovation"]},
        },
        "trigger": {"type": "once"},
    }
    value.update(overrides)
    return value


def test_plan_defaults_to_bounded_rolling_day_and_round_trips():
    parsed = parse_task_plan(_plan())

    assert parsed.window.type == "rolling"
    assert parsed.window.days == 1
    assert task_plan_block(parsed)["source_scope"] == "discovery"
    payload = apply_task_plan({}, _plan(), job_type="analyze")
    assert payload["collection"]["limit"] == 100
    assert "cli_dates" not in payload


def test_selected_scope_requires_real_workspace_source_ids():
    with pytest.raises(TaskPlanError, match="task_plan_selected_sources_required"):
        parse_task_plan(_plan(source_scope="selected"))

    parsed = parse_task_plan(_plan(source_scope="selected", source_ids=[12, 13]))
    assert parsed.source_ids == (12, 13)


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"source_scope": "discovery", "collection": {"limit": 10}}, "task_plan_discovery_queries_required"),
        ({"source_scope": "all", "source_ids": [2]}, "task_plan_source_ids_scope_conflict"),
        ({"platforms": ["filesystem"]}, "task_plan_platform_unsupported"),
        ({"trigger": {"type": "schedule", "cron": "@once"}}, "task_plan_schedule_requires_recurring_cron"),
        ({"window": {"type": "rolling", "days": 0}}, "task_plan_rolling_days_invalid"),
    ],
)
def test_malformed_scope_trigger_or_window_fails_closed(overrides, code):
    with pytest.raises(TaskPlanError, match=code):
        parse_task_plan(_plan(**overrides))


def test_fixed_window_is_canonical_and_mirrored_for_existing_collectors():
    request = _plan(
        source_scope="selected",
        source_ids=[7],
        window={"type": "fixed", "start": "2026-10-01", "end": "2026-10-25"},
    )
    payload = apply_task_plan({}, request, job_type="collect")

    assert payload["cli_dates"] == {"start_date": "2026-10-01", "end_date": "2026-10-25"}
    assert payload["collection"]["date_window"] == {"start": "2026-10-01", "end": "2026-10-25"}


def test_collection_end_without_start_cannot_create_unbounded_fixed_plan():
    with pytest.raises(TaskPlanError, match="task_plan_fixed_start_required"):
        parse_task_plan(
            _plan(
                source_scope="selected",
                source_ids=[7],
                collection={"date_window": {"end": "2026-10-25"}},
            )
        )


def test_rolling_window_materializes_once_and_replay_keeps_same_bounds():
    payload = apply_task_plan({}, _plan(), job_type="analyze")
    admitted_at = datetime(2026, 10, 25, 23, 58, tzinfo=timezone.utc)

    admitted = materialize_task_plan_window(payload, admitted_at)
    replay = materialize_task_plan_window(admitted, datetime(2026, 10, 26, 0, 1, tzinfo=timezone.utc))

    expected = {"start_date": date(2026, 10, 25).isoformat(), "end_date": date(2026, 10, 25).isoformat()}
    assert admitted["cli_dates"] == expected
    assert replay["cli_dates"] == expected
    assert replay["task_plan"]["window"] == {
        "type": "fixed",
        "start": expected["start_date"],
        "end": expected["end_date"],
    }


def test_task_plan_cannot_be_applied_to_digest_payload():
    with pytest.raises(TaskPlanError, match="task_plan_requires_collect_or_analyze"):
        apply_task_plan({}, _plan(), job_type="digest")
