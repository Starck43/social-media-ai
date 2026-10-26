"""CLI previews use the shared plan contract without database access."""

import json

import pytest
from typer.testing import CliRunner

from cli.main import app

__source_only__ = True


def test_cli_task_plan_shows_default_window_without_creating_work():
    plan = {"goal": "Research VK", "source_scope": "discovery", "collection": {"queries": ["repair"]}}
    result = CliRunner().invoke(app, ["task", "plan", json.dumps(plan), "--job-type", "collect"])
    assert result.exit_code == 0, result.output
    assert '"rolling"' in result.output
    assert '"days": 1' in result.output
    assert "Preview only" in result.output


@pytest.mark.parametrize("invalid", ["null", "[]", "not-json", '{"source_scope":"selected"}'])
def test_cli_task_plan_refuses_malformed_or_empty_selection(invalid):
    result = CliRunner().invoke(app, ["task", "plan", invalid])
    assert result.exit_code == 1
    assert "Invalid task plan" in result.output
