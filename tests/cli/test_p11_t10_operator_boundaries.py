"""No-DB policy checks for P11-T10 operator and workspace boundaries."""

from __future__ import annotations

import ast
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from cli.commands import tasks
from cli.commands.task_output import emit_task_json, task_output_format, task_print

__source_only__ = True
ROOT = Path(__file__).resolve().parents[2]


def _task_source():
    return (ROOT / "cli" / "commands" / "tasks.py").read_text(encoding="utf-8")


def _functions():
    tree = ast.parse(_task_source())
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_task_group_fails_closed_without_operator_identity(monkeypatch):
    def reject():
        raise tasks.OperatorIdentityRequired

    monkeypatch.setattr(tasks, "_require_operator_context", reject)

    with pytest.raises(typer.Exit) as exc:
        tasks._task_operator_boundary(output="human")

    assert exc.value.exit_code == tasks.EXIT_OPERATOR_REQUIRED == 4


@pytest.mark.parametrize("command", ["task_add", "task_update", "task_remove", "task_pause", "run"])
def test_mutating_task_commands_require_workspace_argument(command):
    function = _functions()[command]
    parameters = function.args.args + function.args.kwonlyargs
    defaults = [None] * (len(parameters) - len(function.args.defaults)) + list(function.args.defaults)
    defaults += [None] * len(function.args.kwonlyargs)
    if function.args.kw_defaults:
        defaults[-len(function.args.kwonlyargs) :] = function.args.kw_defaults
    tenant_default = next(
        (
            default
            for parameter, default in zip(parameters, defaults)
            if parameter.arg == "tenant"
        ),
        None,
    )

    assert isinstance(tenant_default, ast.Call)
    assert isinstance(tenant_default.func, ast.Attribute)
    assert tenant_default.func.attr == "Option"
    assert isinstance(tenant_default.args[0], ast.Constant)
    assert tenant_default.args[0].value is Ellipsis


@pytest.mark.parametrize("command", ["_remove_task", "task_pause", "task_update", "_run_task"])
def test_target_lookup_is_explicitly_scoped(command):
    source = ast.unparse(_functions()[command])

    assert "tenant_id=tenant_id" in source
    assert "_require_explicit_tenant(tenant)" in source


def test_task_operator_revalidation_binds_a_fresh_actor():
    source = ast.unparse(_functions()["_run_task_platform"])

    assert "prefetch_related" in source
    assert "role" in source
    assert "is_platform_admin" in source
    assert "permission_scope(fresh_actor)" in source
    assert "operator_permission_scope()" in source


def test_task_group_declares_output_format_option():
    source = ast.unparse(_functions()["_task_operator_boundary"])

    assert "--output" in source
    assert "human" in source
    assert "json" in source


def test_json_envelope_is_versioned_and_redacts_credentials():
    stream = StringIO()
    with redirect_stdout(stream), task_output_format("json"):
        emit_task_json(
            "task probe",
            0,
            {
                "status": "done",
                "api_key": "do-not-print",
                "summary": "Bearer secret-value",
            },
        )

    result = json.loads(stream.getvalue())
    assert result == {
        "schema_version": 1,
        "command": "task probe",
        "ok": True,
        "exit_code": 0,
        "data": {"status": "done", "summary": "[REDACTED]"},
    }


def test_json_mode_suppresses_rich_messages():
    stream = StringIO()
    with redirect_stdout(stream), task_output_format("json"):
        task_print("do-not-mix-human-output")

    assert stream.getvalue() == ""


def test_json_command_hides_unexpected_exception_details():
    @tasks._json_command("task probe")
    def broken():
        raise RuntimeError("Bearer provider-secret")

    stream = StringIO()
    with redirect_stdout(stream), task_output_format("json"), pytest.raises(typer.Exit) as exc:
        broken()

    result = json.loads(stream.getvalue())
    assert exc.value.exit_code == tasks.EXIT_COMMAND_FAILED == 1
    assert result["error"] == {
        "code": "command_failed",
        "message": "Command failed; diagnostic details are omitted.",
    }
    assert "provider-secret" not in stream.getvalue()


def test_typer_json_command_writes_one_envelope(monkeypatch):
    monkeypatch.setattr(tasks, "_require_operator_context", lambda: None)
    app = typer.Typer()
    app.callback()(tasks._task_operator_boundary)

    @app.command("probe")
    @tasks._json_command("task probe")
    def probe():
        return {"status": "done"}

    result = CliRunner().invoke(app, ["--output", "json", "probe"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "schema_version": 1,
        "command": "task probe",
        "ok": True,
        "exit_code": 0,
        "data": {"status": "done"},
    }
    assert result.stdout.count("\n") == 1


def test_typer_operator_failure_uses_exit_code_four_and_json(monkeypatch):
    def reject():
        raise tasks.OperatorIdentityRequired

    monkeypatch.setattr(tasks, "_require_operator_context", reject)
    app = typer.Typer()
    app.callback()(tasks._task_operator_boundary)

    @app.command("probe")
    def probe():
        return None

    result = CliRunner().invoke(app, ["--output", "json", "probe"])
    assert result.exit_code == tasks.EXIT_OPERATOR_REQUIRED == 4
    assert json.loads(result.stdout)["error"]["code"] == "operator_required"


def test_unexpected_operator_check_error_is_generic_json(monkeypatch):
    def crash():
        raise RuntimeError("credential-bearing diagnostic")

    monkeypatch.setattr(tasks, "_require_operator_context", crash)
    stream = StringIO()
    with (
        redirect_stdout(stream),
        task_output_format("json"),
        pytest.raises(typer.Exit) as exc,
    ):
        tasks._task_operator_boundary(output="json")

    assert exc.value.exit_code == tasks.EXIT_COMMAND_FAILED
    result = json.loads(stream.getvalue())
    assert result["error"]["code"] == "command_failed"
    assert "credential-bearing diagnostic" not in stream.getvalue()