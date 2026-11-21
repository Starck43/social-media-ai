"""Slice parity: API, CLI and agent tools expose one operation set (P22-T07)."""

__source_only__ = True

from app.agent.tools import TOOL_REGISTRY
from app.api.v2 import tasks as api
from cli.commands import work_tasks as cli

# One canonical operation per row; every surface must offer exactly these.
API_ROUTES = {
    "list": ("GET", "/tasks"),
    "get": ("GET", "/tasks/{task_id}"),
    "create": ("POST", "/tasks"),
    "edit": ("PATCH", "/tasks/{task_id}"),
    "preview": ("POST", "/tasks/{task_id}/plan/preview"),
    "complete": ("POST", "/tasks/{task_id}/complete"),
    "delete_preview": ("POST", "/tasks/{task_id}/delete/preview"),
    "delete": ("POST", "/tasks/{task_id}/delete"),
    "run": ("POST", "/tasks/{task_id}/runs"),
    "runs": ("GET", "/tasks/{task_id}/runs"),
    "actions": ("GET", "/tasks/{task_id}/actions"),
    "decision_preview": ("POST", "/tasks/{task_id}/decisions/preview"),
    "decide": ("POST", "/tasks/{task_id}/decisions"),
    "resolve_legacy": ("GET", "/tasks/legacy/{namespace}/{legacy_id}"),
}

CLI_COMMANDS = {
    "list": "list",
    "get": "show",
    "create": "create",
    "edit": "edit",
    "preview": "preview",
    "complete": "complete",
    "delete_preview": "delete",
    "delete": "delete",
    "run": "run",
    "runs": "runs",
    "actions": "actions",
    "decision_preview": "decide",
    "decide": "decide",
    "resolve_legacy": "resolve",
}

TOOLS = {
    "list": "work_task_list",
    "get": "work_task_get",
    "create": "work_task_create",
    "edit": "work_task_edit",
    "preview": "work_task_preview",
    "complete": "work_task_complete",
    "delete_preview": "work_task_delete_preview",
    "delete": "work_task_delete",
    "run": "work_task_run",
    "runs": "work_task_runs",
    "actions": "work_task_actions",
    "decision_preview": "work_task_decision_preview",
    "decide": "work_task_decide",
    "resolve_legacy": "work_task_resolve_legacy",
}


def test_api_routes_are_exactly_the_declared_operations():
    found = {(method, route.path) for route in api.router.routes for method in route.methods}
    assert found == set(API_ROUTES.values())


def test_cli_offers_every_operation_and_nothing_else():
    names = {command.name for command in cli.app.registered_commands}
    assert names == set(CLI_COMMANDS.values())


def test_tools_are_registered_for_every_operation():
    assert set(TOOLS.values()) <= set(TOOL_REGISTRY)


def test_no_surface_invents_an_extra_operation():
    assert set(API_ROUTES) == set(CLI_COMMANDS) == set(TOOLS)
    extra = {name for name in TOOL_REGISTRY if name.startswith("work_task_")} - set(TOOLS.values())
    assert extra == set()


def test_write_tools_ask_for_confirmation_and_reads_do_not():
    writes = {"work_task_create", "work_task_edit", "work_task_complete", "work_task_delete", "work_task_run",
              "work_task_decide"}
    for name in TOOLS.values():
        assert TOOL_REGISTRY[name].requires_confirmation is (name in writes), name


def test_every_tool_carries_the_same_canonical_permission():
    for name in TOOLS.values():
        permission = TOOL_REGISTRY[name].required_permission
        assert permission in {
            "planner.task.view",
            "planner.task.create",
            "planner.task.update",
            "planner.task.delete",
        }, (name, permission)
