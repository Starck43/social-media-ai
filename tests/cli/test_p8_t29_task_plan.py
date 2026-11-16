"""CLI previews use the shared plan contract without database access."""

import json

import pytest
from typer.testing import CliRunner

from cli.commands import tasks as task_commands
from cli.main import app

__source_only__ = True


def test_cli_task_plan_shows_default_window_without_creating_work(monkeypatch):
    # The parser-only preview does not resolve a workspace. Satisfy the new
    # task-group operator boundary here; P11-T10 separately tests its denial.
    monkeypatch.setattr(task_commands, "_require_operator_context", lambda: None)
    plan = {"goal": "Research VK", "source_scope": "discovery", "collection": {"queries": ["repair"]}}
    result = CliRunner().invoke(app, ["task", "plan", json.dumps(plan), "--job-type", "collect"])
    assert result.exit_code == 0, result.output
    assert '"rolling"' in result.output
    assert '"days": 1' in result.output
    assert "Preview only" in result.output


@pytest.mark.parametrize("invalid", ["null", "[]", "not-json", '{"source_scope":"selected"}'])
def test_cli_task_plan_refuses_malformed_or_empty_selection(monkeypatch, invalid):
    monkeypatch.setattr(task_commands, "_require_operator_context", lambda: None)
    result = CliRunner().invoke(app, ["task", "plan", invalid])
    assert result.exit_code == 1
    assert "Invalid task plan" in result.output
