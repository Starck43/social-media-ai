"""Shared task-plan create preparation preserves canonical mapping and guards."""

import pytest

from app.services.task_creation import (
    TaskPlanCreateConflict,
    prepare_task_plan_create,
    project_task_plan_create,
    resolve_task_plan_create,
)
from app.services.task_plans import TaskPlanError


def _plan(**updates):
    return {
        "version": 1,
        "goal": "Research requests",
        "platforms": ["vk"],
        "source_scope": "selected",
        "source_ids": [4],
        "collection": {"queries": ["repair"]},
        "window": {"type": "rolling", "days": 3},
        "trigger": {"type": "schedule", "cron": "0 9 * * *", "timezone": "UTC"},
        **updates,
    }


def test_prepare_plan_create_resolves_all_persisted_plan_fields_and_preserves_payload_context():
    prepared = prepare_task_plan_create(
        {"payer_receipt": {"id": 99}},
        _plan(),
        job_type="analyze",
        requested_cron="0 9 * * *",
        requested_source_ids=[4],
        enforce_source_match=True,
    )

    assert prepared.payload is not None
    assert prepared.cron_expr == "0 9 * * *"
    assert prepared.scenario_id is None
    assert prepared.source_scope == "selected"
    assert prepared.source_ids == (4,)
    assert prepared.payload["payer_receipt"] == {"id": 99}
    assert prepared.payload["task_plan"]["goal"] == "Research requests"
    assert prepared.payload["task_plan"]["window"] == {"type": "rolling", "days": 3}
    assert "cli_dates" not in prepared.payload


def test_prepare_plan_create_preserves_empty_source_scope_for_all_and_discovery():
    for scope in ("all", "discovery"):
        prepared = prepare_task_plan_create(
            {},
            _plan(source_scope=scope, source_ids=[]),
            job_type="collect",
        )
        assert prepared.source_scope == scope
        assert prepared.source_ids == ()
        assert prepared.payload is not None
        assert prepared.payload["task_plan"]["source_scope"] == scope


def test_prepare_plan_create_rejects_empty_selected_scope():
    with pytest.raises(TaskPlanError):
        prepare_task_plan_create({}, _plan(source_ids=[]), job_type="collect")


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"requested_cron": "0 8 * * *"}, "cron_expr"),
        ({"requested_scenario_id": 7}, "scenario_id"),
        ({"requested_source_ids": [], "enforce_source_match": True}, "source_ids"),
    ],
)
def test_prepare_plan_create_rejects_conflicting_legacy_fields(kwargs, field):
    with pytest.raises(TaskPlanCreateConflict) as error:
        prepare_task_plan_create({}, _plan(), job_type="collect", **kwargs)

    assert error.value.field == field


def test_prepare_plan_create_keeps_surface_conflict_order():
    with pytest.raises(TaskPlanCreateConflict) as error:
        prepare_task_plan_create(
            {},
            _plan(),
            job_type="collect",
            requested_cron="0 8 * * *",
            requested_source_ids=[],
            enforce_source_match=True,
            conflict_order=("source_ids", "cron_expr", "scenario_id"),
        )

    assert error.value.field == "source_ids"


def test_create_resolution_defers_job_type_error_until_payload_projection():
    resolved = resolve_task_plan_create(_plan(), requested_cron="0 9 * * *")

    assert resolved.cron_expr == "0 9 * * *"
    with pytest.raises(TaskPlanError, match="task_plan_requires_collect_or_analyze"):
        project_task_plan_create({}, resolved, job_type="digest")


def test_prepare_plan_create_projects_fixed_window_and_one_off_trigger():
    fixed = prepare_task_plan_create(
        {},
        _plan(window={"type": "fixed", "start": "2026-01-02", "end": "2026-01-04"}),
        job_type="collect",
    )
    once = prepare_task_plan_create({}, _plan(trigger={"type": "once"}), job_type="analyze")

    assert fixed.payload is not None
    assert fixed.payload["cli_dates"] == {"start_date": "2026-01-02", "end_date": "2026-01-04"}
    assert once.cron_expr == "@once"
    assert once.payload is not None
    assert once.payload["task_plan"]["trigger"] == {"type": "once"}
