"""No-DB policy checks for P11-T10 operator and workspace boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import typer

from cli.commands import tasks

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
        tasks._task_operator_boundary()

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