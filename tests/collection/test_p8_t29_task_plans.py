"""Shared social task-plan validation; pure tests with no database/provider calls."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.task_plans import (
    TaskPlanError,
    apply_task_plan,
    apply_task_plan_override,
    materialize_task_plan_window,
    parse_task_plan,
    task_plan_block,
    task_plan_timezone,
)


__source_only__ = True

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
        (
            {"collection": {"queries": [f"phrase {index}" for index in range(6)]}},
            "task_plan_query_limit_exceeded",
        ),
        (
            {
                "collection": {
                    "discovery": {
                        "communities": True,
                        "queries": [f"phrase {index}" for index in range(6)],
                    }
                }
            },
            "task_plan_query_limit_exceeded",
        ),
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


def test_explicit_schedule_timezone_controls_next_fire_and_legacy_uses_fallback():
    from app.tasks.cron import next_run_at

    request = _plan(trigger={"type": "schedule", "cron": "0 9 * * *", "timezone": "Europe/Kirov"})
    plan = parse_task_plan(request)
    zone = task_plan_timezone(task_plan_block(plan), "UTC")

    assert zone == "Europe/Kirov"
    assert next_run_at("0 9 * * *", zone, datetime(2026, 10, 25, 5, tzinfo=timezone.utc)) == datetime(
        2026, 10, 25, 6, tzinfo=timezone.utc
    )
    assert task_plan_timezone(None, "Europe/Moscow") == "Europe/Moscow"


def test_schedule_timezone_must_be_a_real_iana_zone():
    with pytest.raises(TaskPlanError, match="task_plan_timezone_invalid"):
        parse_task_plan(_plan(trigger={"type": "schedule", "cron": "0 9 * * *", "timezone": "Mars/Olympus"}))


def test_per_run_plan_override_normalizes_window_but_cannot_change_scope():
    persisted = apply_task_plan({}, _plan(source_scope="selected", source_ids=[12]), job_type="collect")
    requested = _plan(
        source_scope="selected",
        source_ids=[12],
        window={"type": "fixed", "start": "2026-10-20", "end": "2026-10-25"},
    )

    overridden = apply_task_plan_override(persisted, requested, job_type="collect")

    assert overridden["cli_dates"] == {"start_date": "2026-10-20", "end_date": "2026-10-25"}
    with pytest.raises(TaskPlanError, match="task_plan_override_changes_persisted_scope"):
        apply_task_plan_override(persisted, _plan(source_scope="all"), job_type="collect")


def test_remaining_plan_collection_context_narrows_parent_post_budget_only():
    from app.jobs.handlers import _consume_task_parent_budget, _remaining_collection_context
    from app.services.collection.context import build_collection_context

    context = build_collection_context({"collection": {"limit": 80}}, platform="vk")
    narrowed = _remaining_collection_context(context, 17)

    assert context.intent.limit == 80
    assert narrowed.intent.limit == 17
    assert narrowed.intent.comments.total == context.intent.comments.total
    assert narrowed.intent.candidates.limit == context.intent.candidates.limit
    budget = {"limit": 10, "parent_posts_inspected": 0, "limit_reached": False}
    assert _consume_task_parent_budget(budget, 4) is False
    assert _consume_task_parent_budget(budget, 6) is True
    assert budget["parent_posts_inspected"] == 10


def test_task_generated_l2_mode_is_per_run_and_selected_source_mode_is_preserved():
    from app.jobs.handlers import _runtime_source

    generated = SimpleNamespace(
        id=1,
        tenant_id=3,
        name="Generated wall",
        source_type="GROUP",
        external_id="-10",
        platform_id=1,
        platform="vk",
        params={"mode": "auto"},
        owner_task_id=7,
    )
    selected = SimpleNamespace(**{**generated.__dict__, "id": 2, "owner_task_id": None, "params": {"mode": "api"}})

    runtime_generated = _runtime_source(
        generated,
        task_id=7,
        task_plan_enabled=True,
        owner_user_id=42,
        cli_dates=None,
        force_refresh=False,
    )
    runtime_selected = _runtime_source(
        selected,
        task_id=7,
        task_plan_enabled=True,
        owner_user_id=42,
        cli_dates=None,
        force_refresh=False,
    )

    assert runtime_generated.params == {"mode": "user", "token_owner": 42}
    assert runtime_selected.params == {"mode": "api"}
    assert generated.params == {"mode": "auto"}
    assert selected.params == {"mode": "api"}
