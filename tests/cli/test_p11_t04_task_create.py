"""P11-T04 task-plan creation keeps CLI transport mapping and scope checks."""

import json

import pytest
from typer.testing import CliRunner

from cli import main as cli_main


def _plan(*, fixed_window: bool) -> dict:
    return {
        "goal": "Research repairs",
        "platforms": ["vk"],
        "source_scope": "selected",
        "source_ids": [17],
        "scenario_id": 23,
        "collection": {"queries": ["repair"]},
        "window": (
            {"type": "fixed", "start": "2026-10-01", "end": "2026-10-14"}
            if fixed_window
            else {"type": "rolling", "days": 2}
        ),
        "trigger": ({"type": "schedule", "cron": "0 6 * * *"} if fixed_window else {"type": "once"}),
    }


@pytest.mark.parametrize(
    ("fixed_window", "cron"),
    [(False, "@once"), (True, "0 6 * * *")],
    ids=["one-off-rolling", "scheduled-fixed"],
)
def test_task_add_passes_canonical_plan_fields_to_persistence(monkeypatch, fixed_window, cron):
    captured = {}

    async def capture_add(**values):
        captured.update(values)

    monkeypatch.setattr(cli_main, "_add_task", capture_add)
    result = CliRunner().invoke(
        cli_main.app,
        [
            "task",
            "add",
            "research",
            "--cron",
            cron,
            "--job-type",
            "collect",
            "--task-plan",
            json.dumps(_plan(fixed_window=fixed_window)),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["cron"] == cron
    assert captured["parsed_source_ids"] == [17]
    assert captured["scenario_id"] == 23
    assert captured["parsed_payload"]["task_plan"]["source_scope"] == "selected"
    assert captured["parsed_payload"]["task_plan"]["source_ids"] == [17]
    assert captured["parsed_payload"]["task_plan"]["window"]["type"] == ("fixed" if fixed_window else "rolling")


def test_task_add_rejects_source_conflict_before_persistence(monkeypatch):
    async def fail_if_persisted(**_values):
        pytest.fail("conflicting source selection must be rejected before persistence")

    monkeypatch.setattr(cli_main, "_add_task", fail_if_persisted)
    result = CliRunner().invoke(
        cli_main.app,
        [
            "task",
            "add",
            "research",
            "--cron",
            "@once",
            "--job-type",
            "collect",
            "--sources",
            "18",
            "--task-plan",
            json.dumps(_plan(fixed_window=False)),
        ],
    )

    assert result.exit_code == 1
    assert "--sources must match task_plan.source_scope and source_ids" in result.output


def test_task_add_reports_unknown_job_type_before_parsing_a_valid_plan(monkeypatch):
    async def fail_if_persisted(**_values):
        pytest.fail("an unsupported task type must not reach persistence")

    monkeypatch.setattr(cli_main, "_add_task", fail_if_persisted)
    result = CliRunner().invoke(
        cli_main.app,
        [
            "task",
            "add",
            "research",
            "--cron",
            "@once",
            "--job-type",
            "unsupported",
            "--task-plan",
            json.dumps(_plan(fixed_window=False)),
        ],
    )

    assert result.exit_code == 1
    assert "job_type must be one of" in result.output
